# Based on https://github.com/powerline/fontpatcher/blob/develop/scripts/powerline-fontpatcher
# Used under the MIT license

import argparse
import sys
import re
import subprocess
import os.path
import json
import shutil
import zipfile

from itertools import chain
from collections import defaultdict

try:
    import fontforge
    import psMat
    # from fontTools.misc.py23 import *
    from fontTools.ttLib import TTFont
    from fontTools.feaLib.builder import addOpenTypeFeatures, Builder
except ImportError:
    sys.stderr.write('The required FontForge and fonttools modules could not be loaded.\n\n')
    sys.stderr.write('You need FontForge with Python bindings for this script to work.\n')
    sys.exit(1)

ALL_XDIGITS = "0123456789abcdefABCDEF"


def get_argparser(ArgumentParser=argparse.ArgumentParser):
    parser = ArgumentParser(
        description=('Font patcher for Numderline. '
                     'Requires FontForge with Python bindings. '
                     'Stores the patched font as a new, renamed font file by default.')
    )
    parser.add_argument('target_fonts', help='font files to patch', metavar='font',
                        nargs='*', type=argparse.FileType('rb'))
    parser.add_argument('--group',
                        help='group squished digits in threes, shorthand for --no-underline --shift-amount 100 --squish 0.85 --squish-all',
                        default=False, action='store_true')
    parser.add_argument('--no-rename',
                        help='don\'t add " with Numderline" to the font name',
                        default=True, action='store_false', dest='rename_font')
    parser.add_argument('--no-underline',
                        help='don\'t add underlines',
                        default=True, action='store_false', dest='add_underlines')
    parser.add_argument('--no-decimals',
                        help='don\'t touch digits after the decimal point',
                        default=True, action='store_false', dest='do_decimals')
    parser.add_argument('--add-commas',
                        help='add commas',
                        default=False, action='store_true')
    parser.add_argument('--shift-amount', help='amount to shift digits to group them together, try 100', type=int,
                        default=0)
    parser.add_argument('--squish',
                        help='horizontal scale to apply to the digits to maybe make them more readable when shifted',
                        type=float, default=1.0)
    parser.add_argument('--squish-all',
                        help='squish all numbers, including decimals and ones less than 4 digits, use with --squish flag',
                        default=False, action='store_true')
    parser.add_argument('--sub-font', help='substitute alternating groups of 3 with this font',
                        type=argparse.FileType('rb'))
    parser.add_argument('--spaceless-commas',
                        help='manipulate commas to not change the spacing, for monospace fonts, use with --add-commas',
                        default=True, action='store_true')
    parser.add_argument('--debug-annotate',
                        help='annotate glyph copies with debug digits',
                        default=False, action='store_true')
    parser.add_argument('--build-release',
                        help='build the default release of Numderline',
                        default=False, action='store_true')
    return parser


FONT_NAME_RE = re.compile(r'^([^-]*)(?:(-.*))?$')
ITERM_LIGA_BLACKLIST = [
    "AndaleMono", "Courier", "LetterGothicStd", "Menlo",
    "Monaco", "OCRAStd", "OratorStd", "Osaka", "PTMono", "SFMono",
]
NUM_DIGIT_COPIES = [0, 1, 2, 3, 4, 5, 6, 7, 8]


def gen_feature(digit_names, xdigit_names, underscore_name, dot_name, do_decimals):
    if do_decimals:
        decimal_sub = """
    ignore sub {dot_name} {dot_name} @digits';
    sub {dot_name} @digits' by @nd2;
    sub @nd2 @digits' by @nd1;
    sub @nd1 @digits' by @nd6;
    sub @nd6 @digits' by @nd5;
    sub @nd5 @digits' by @nd4;
    sub @nd4 @digits' by @nd3;
    sub @nd3 @digits' by @nd2;
"""
    else:
        decimal_sub = """
    ignore sub {dot_name} @digits';
    sub @digits @digits' by @digits;
"""

    decimal_sub = decimal_sub.format(dot_name=dot_name)

    feature = """
languagesystem DFLT dflt;
languagesystem latn dflt;
languagesystem cyrl dflt;
languagesystem grek dflt;
languagesystem kana dflt;
@digits=[{digit_names}];
@xdigits=[{xdigit_names}];
{nds}

# final variations
@nda=[{xdigitA_names}];
@ndb=[{xdigitB_names}];

## Lookups are executed in the order they are listed below, regardless of the
## order they are referenced in the feature rules that follow.

lookup RA {{
  sub @xdigits by @nda;
}} RA;

lookup RB {{
  sub @xdigits by @ndb;
}} RB;

lookup START_HEX {{

    sub zero [x X] @xdigits' by @xnd0;
    sub @xnd0 @xdigits' by @xnd0;

}} START_HEX;

lookup FLOW_HEX {{
    reversesub @xnd0' @xnd0 by @xnd8;
    
    reversesub @xnd0' @xnd1 by @xnd2;
    reversesub @xnd0' @xnd2 by @xnd3;
    reversesub @xnd0' @xnd3 by @xnd4;
    reversesub @xnd0' @xnd4 by @xnd5;
    reversesub @xnd0' @xnd5 by @xnd7;
    reversesub @xnd0' @xnd7 by @xnd6;
    reversesub @xnd0' @xnd6 by @xnd8;
    reversesub @xnd0' @xnd8 by @xnd1;
}} FLOW_HEX;

lookup REP_DECIMALS {{
    {decimal_sub}
}} REP_DECIMALS;

# Use a very rough heuristic to detect 8 digit numbers that roughly match an ISO8601 YYYYMMDD.
# If matched, underline the year and day. 
#
lookup REP_ISO {{
  ignore sub zero [x X] @digits' @digits' @digits' @digits' @digits' @digits' @digits' @digits';
  ignore sub @digits @digits' @digits' @digits' @digits' @digits' @digits' @digits' @digits';
  ignore sub @digits' @digits' @digits' @digits' @digits' @digits' @digits' @digits' @digits;

  sub [one two]' lookup RB
      [nine zero one]' lookup RB
      @digits' lookup RB
      @digits' lookup RB
      [zero one]' lookup RA
      @digits' lookup RA
      [zero one two three]' lookup RB
      @digits' lookup RB;

}} REP_ISO;

lookup REP_BARE_X8 {{
  ignore sub
    @xdigits
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits';

  ignore sub
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits;

  sub @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA;

}} REP_BARE_X8;

lookup REP_BARE_X12 {{
  ignore sub
    @xdigits
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits';

  ignore sub
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits;

  sub @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA;

}} REP_BARE_X12;

lookup REP_BARE_X16 {{
  ignore sub
    @xdigits
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits';

  ignore sub
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits' @xdigits' @xdigits' @xdigits'
    @xdigits;

  sub @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RB
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA
      @xdigits' lookup RA
;
}} REP_BARE_X16;

lookup START_DIGITS {{

    sub @digits' @digits @digits @digits by @nd0;
    sub @nd0 @digits' by @nd0;

}} START_DIGITS;

lookup FLOW_DIGITS {{
    reversesub @nd0' @nd0 by @nd1;
    reversesub @nd0' @nd1 by @nd2;
    reversesub @nd0' @nd2 by @nd3;
    reversesub @nd0' @nd3 by @nd4;
    reversesub @nd0' @nd4 by @nd5;
    reversesub @nd0' @nd5 by @nd6;
    reversesub @nd0' @nd6 by @nd1;
}} FLOW_DIGITS;

feature liga {{
  
  # hex
  lookup START_HEX;
  lookup FLOW_HEX;

  lookup REP_DECIMALS;
  
  # special handling
  lookup REP_ISO;

  lookup START_DIGITS;
  lookup FLOW_DIGITS;
}} liga;


"""[1:]

    nds = [' '.join(['nd{}.{}'.format(i, j) for j in range(10)]) for i in NUM_DIGIT_COPIES]
    nds = ['@nd{}=[{}];'.format(name, nds[i]) for i, name in enumerate(NUM_DIGIT_COPIES)]

    xnds = [' '.join(['xnd{}.{}'.format(i, j) for j in ALL_XDIGITS]) for i in NUM_DIGIT_COPIES]
    xnds = ['@xnd{}=[{}];'.format(name, xnds[i]) for i, name in enumerate(NUM_DIGIT_COPIES)]

    nds = "\n".join(nds + xnds)

    xdigitA_names = ' '.join(['xndA.{}'.format(j) for j in ALL_XDIGITS])
    xdigitB_names = ' '.join(['xndB.{}'.format(j) for j in ALL_XDIGITS])

    feature = feature.format(digit_names=' '.join(digit_names), xdigit_names=' '.join(xdigit_names),
                             nds=nds, underscore_name=underscore_name, decimal_sub=decimal_sub,
                             xdigitA_names=xdigitA_names, xdigitB_names=xdigitB_names)
    with open('mods.fea', 'w') as f:
        f.write(feature)


def shift_layer(layer, shift):
    layer = layer.dup()
    mat = psMat.translate(shift, 0)
    layer.transform(mat)
    return layer


def squish_layer(layer, squish):
    layer = layer.dup()
    mat = psMat.scale(squish, 1.0)
    layer.transform(mat)
    return layer


def add_comma_to(glyph, comma_glyph, spaceless):
    comma_layer = comma_glyph.layers[1].dup()
    x_shift = glyph.width
    y_shift = 0
    if spaceless:
        mat = psMat.scale(0.8, 0.8)
        comma_layer.transform(mat)
        x_shift -= comma_glyph.width * 0.35
        y_shift -= comma_glyph.width * 0.2
        # y_shift = -200
    mat = psMat.translate(x_shift, y_shift)
    comma_layer.transform(mat)
    glyph.layers[1] += comma_layer
    if not spaceless:
        glyph.width += comma_glyph.width


def annotate_glyph(glyph, extra_glyph):
    layer = extra_glyph.layers[1].dup()
    mat = psMat.translate(-(extra_glyph.width / 2), 0)
    layer.transform(mat)
    mat = psMat.scale(0.3, 0.3)
    layer.transform(mat)
    mat = psMat.translate((extra_glyph.width / 2), 0)
    layer.transform(mat)
    mat = psMat.translate(0, -600)
    layer.transform(mat)
    glyph.layers[1] += layer


def out_path(name):
    return 'out/{0}.ttf'.format(name)


def patch_one_font(font, rename_font, add_underlines, shift_amount, squish, squish_all, add_commas, spaceless_commas,
                   debug_annotate, do_decimals, group, sub_font):
    font.encoding = 'ISO10646'

    if group:
        add_underlines = False
        shift_amount = 100
        squish = 0.85
        squish_all = True

    mod_name = 'N'
    if add_commas:
        if spaceless_commas:
            mod_name += 'onoCommas'
        else:
            mod_name += 'ommas'
    if add_underlines:
        mod_name += 'umderline'
    if sub_font is not None:
        mod_name += 'Sub'

    mod_name = 'Hexderline'

    # Cleaner name for what I expect to be a common combination
    if shift_amount == 100 and squish == 0.85 and squish_all:
        mod_name += 'Group'
    else:
        if shift_amount != 0:
            mod_name += 'Shift{}'.format(shift_amount)
        if squish != 1.0:
            squish_s = '{}'.format(squish)
            mod_name += 'Squish{}'.format(squish_s.replace('.', 'p'))
            if squish_all:
                mod_name += 'All'
    if debug_annotate:
        mod_name += 'Debug'
    if not do_decimals:
        mod_name += 'NoDecimals'

    # Rename font
    if rename_font:
        def renamer(name, with_, mod):
            prefix = ''
            if any(name.startswith(font_name) for font_name in ITERM_LIGA_BLACKLIST):
                prefix = 'X'
            return prefix + name + with_ + mod

        font.familyname = renamer(font.familyname, ' with ', mod_name)
        font.fullname = renamer(font.fullname, ' with ', mod_name)

        fontname, style = FONT_NAME_RE.match(font.fontname).groups()
        font.fontname = renamer(fontname, 'With', mod_name + (style or ''))

        font.appendSFNTName(
            'English (US)', 'Preferred Family', font.familyname)
        font.appendSFNTName(
            'English (US)', 'Compatible Full', font.fullname)

    xdigit_ords = list(range(ord('0'), ord('9') + 1)) + list(range(ord('a'), ord('f') + 1)) + list(
        range(ord('A'), ord('F') + 1))

    digit_names = [font[code].glyphname for code in range(ord('0'), ord('9') + 1)]
    xdigit_names = [font[code].glyphname for code in xdigit_ords]

    test_names = [font[code].glyphname for code in range(ord('A'), ord('J') + 1)]
    underscore_name = font[ord('_')].glyphname
    dot_name = font[ord('.')].glyphname
    # print(digit_names)

    if sub_font is not None:
        sub_font = fontforge.open(sub_font.name)

    underscore_layer = font[underscore_name].layers[1]

    # 0xE900 starts an area spanning until 0xF000 that as far as I can tell nothing
    # popular uses. I checked the Apple glyph browser and Nerd Font.
    # Uses an array because of python closure capture semantics
    encoding_alloc = [0xE900]

    def make_copy(src_font, loc, to_name, add_underscore, add_comma, shift, squish, annotate_with):
        encoding = encoding_alloc[0]
        src_font.selection.select(loc)
        src_font.copy()
        font.selection.select(encoding)
        font.paste()
        glyph = font[encoding]
        glyph.glyphname = to_name
        if squish != 1.0:
            glyph.layers[1] = squish_layer(glyph.layers[1], squish)
        if shift != 0:
            glyph.layers[1] = shift_layer(glyph.layers[1], shift)
        if add_underscore:
            glyph.layers[1] += underscore_layer
        if add_comma:
            add_comma_to(glyph, font[ord(',')], spaceless_commas)
        if annotate_with is not None:
            annotate_glyph(glyph, annotate_with)
        encoding_alloc[0] += 1

    def build_copy(base, copy_i, digit_i):
        shift = 0
        int_copy = type(copy_i) == int
        if int_copy:
            if copy_i % 3 == 0:
                shift = -shift_amount
            elif copy_i % 3 == 2:
                shift = shift_amount
        in_alternating_group = ((3 <= copy_i < 6) or copy_i == 7) if int_copy else copy_i == 'B'
        add_underscore = add_underlines and in_alternating_group
        add_comma = add_commas and int_copy and (copy_i == 3 or copy_i == 6)
        annotate_with = font[xdigit_names[copy_i] if int_copy else copy_i] if debug_annotate else None
        use_sub_font = (sub_font is not None) and in_alternating_group
        src_font = sub_font if use_sub_font else font
        make_copy(src_font, xdigit_names[digit_i], base + '{}.{}'.format(copy_i, ALL_XDIGITS[digit_i]), add_underscore,
                  add_comma, shift, squish, annotate_with)

    for copy_i in list(NUM_DIGIT_COPIES) + ['A', 'B']:
        for digit_i in range(0, 10 + 6 + 6):
            # add_underlines = False
            # add_commas = True
            build_copy('nd', copy_i, digit_i)
            # add_underlines = True
            # add_commas = False
            build_copy('xnd', copy_i, digit_i)

    if squish_all and squish != 1.0:
        for digit in digit_names:
            glyph = font[digit]
            glyph.layers[1] = squish_layer(glyph.layers[1], squish)

    gen_feature(digit_names, xdigit_names, underscore_name, dot_name, do_decimals)

    font.generate('out/tmp.ttf')
    ft_font = TTFont('out/tmp.ttf')
    addOpenTypeFeatures(ft_font, 'mods.fea', tables=['GSUB'])
    # replacement to comply with SIL Open Font License
    out_name = font.fullname.replace('Source ', 'Sauce ')
    ft_font.save(out_path(out_name))
    print("> Created '{}'".format(out_name))

    if sub_font is not None:
        sub_font.close()

    return out_name


def patch_fonts(target_files, *args):
    res = None
    for target_file in target_files:
        target_font = fontforge.open(target_file.name)
        try:
            res = patch_one_font(target_font, *args)
        finally:
            target_font.close()
    return res


def source_font_path(weight, is_italic):
    suffix = weight
    if is_italic and weight == 'Regular':
        suffix = ''
    if is_italic:
        suffix += 'It'
    return "fonts/source-code-pro/SourceCodePro-{}.ttf".format(suffix)


def build_release():
    infos = {
        'DejaVuSansMono': {'prefix': 'fonts/dejavu/DejaVuSansMono',
                           'suffixes': ['', '-Bold', '-Oblique', '-BoldOblique']},
        'DejaVuSans': {'prefix': 'fonts/dejavu/DejaVuSans', 'suffixes': ['', '-Bold', '-Oblique', '-BoldOblique']},
        'UbuntuMono': {'prefix': 'fonts/ubuntu/UbuntuMono', 'suffixes': ['-R', '-B', '-RI', '-BI']},
        'Hack': {'prefix': 'fonts/hack/Hack', 'suffixes': ['-Regular', '-Bold', '-Italic', '-BoldItalic']},
        'SauceCode': {'prefix': 'fonts/source-code-pro/SourceCodePro',
                      'suffixes': ['-Regular', '-Light', '-Bold', '-It', '-BoldIt']},
    }

    to_build = [
        ('debug', 'DejaVuSansMono', ['fonts/dejavu/DejaVuSansMono.ttf', '--no-underline', '--debug-annotate'])
        # ['fonts/source-code-pro/SourceCodePro-Light.ttf', '--no-underline', '--sub-font', 'fonts/source-code-pro/SourceCodePro-Regular.ttf']
    ]

    def for_all_weights(name, set_name, opts):
        info = infos[name]
        for suffix in info['suffixes']:
            to_build.append((set_name, name, ['{}{}.ttf'.format(info['prefix'], suffix)] + opts))

    for name in ['DejaVuSansMono', 'UbuntuMono', 'SauceCode']:
        for_all_weights(name, 'numderline', [])

    for_all_weights('DejaVuSans', 'nommas', ['--no-underline', '--add-commas', '--no-decimals'])

    all_monospace = ['DejaVuSansMono', 'UbuntuMono', 'Hack', 'SauceCode']
    for name in all_monospace:
        for_all_weights(name, 'ngroup', ['--no-underline', '--group'])
    for name in all_monospace:
        for_all_weights(name, 'monocommas',
                        ['--no-underline', '--group', '--add-commas', '--spaceless-commas', '--no-decimals'])

    source_sub_series = ['Regular', 'Semibold', 'Bold', 'Black']
    for i, weight in enumerate(source_sub_series[:-1]):
        for it in [False, True]:
            bolder = source_sub_series[i + 1]
            to_build.append(('nbold', 'SauceCode', [source_font_path(weight, it), '--no-underline', '--sub-font',
                                                    source_font_path(bolder, it)]))

    manifest = defaultdict(lambda: defaultdict(lambda: []))
    for set_name, font_id, args in to_build:
        out_name = main(args)
        if out_name is None:
            raise Exception("Build failed")
        manifest[set_name][font_id].append(out_name)

    print(manifest)

    for variant, fonts in manifest.items():
        for font, weights in fonts.items():
            archive_name = "{}-{}".format(font, variant)
            with zipfile.ZipFile("site/downloads/{}.zip".format(archive_name), 'w',
                                 compression=zipfile.ZIP_DEFLATED) as myzip:
                for weight in weights:
                    myzip.write(out_path(weight), '{}/{}.ttf'.format(archive_name, weight))

    with open('site/_data/manifest.json', 'w') as f:
        f.write(json.dumps(manifest))

    for fonts in manifest.values():
        for weights in fonts.values():
            subprocess.run(['woff2_compress', out_path(weights[0])])
            shutil.copyfile("out/{}.woff2".format(weights[0]), "site/fonts/{}.woff2".format(weights[0]))


def main(argv):
    args = get_argparser().parse_args(argv)

    if args.build_release:
        build_release()
        return

    return patch_fonts(args.target_fonts, args.rename_font, args.add_underlines, args.shift_amount, args.squish,
                       args.squish_all,
                       args.add_commas, args.spaceless_commas, args.debug_annotate, args.do_decimals, args.group,
                       args.sub_font)


main(sys.argv[1:])
