import argparse
import glob
import os
import platform
import shutil
import subprocess

from cibuildpkg import Builder, Package, fetch, get_platform, log_group, run

plat = platform.system()

library_group = [
    Package(
        name="xz",
        source_url="https://github.com/tukaani-project/xz/releases/download/v5.4.4/xz-5.4.4.tar.xz",
        build_arguments=[
            "--disable-doc",
            "--disable-lzma-links",
            "--disable-lzmadec",
            "--disable-lzmainfo",
            "--disable-nls",
            "--disable-scripts",
            "--disable-xz",
            "--disable-xzdec",
        ],
    ),
    Package(
        name="gmp",
        source_url="https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz",
        # out-of-tree builds fail on Windows
        build_dir=".",
    ),
    Package(
        name="xml2",
        requires=["xz"],
        source_url="https://download.gnome.org/sources/libxml2/2.9/libxml2-2.9.13.tar.xz",
        build_arguments=["--without-python"],
    ),
]

gnutls_group = [
    Package(
        name="unistring",
        source_url="https://ftp.gnu.org/gnu/libunistring/libunistring-1.2.tar.gz",
    ),
    Package(
        name="nettle",
        requires=["gmp"],
        source_url="https://ftp.gnu.org/gnu/nettle/nettle-3.9.1.tar.gz",
        build_arguments=["--disable-documentation"],
        # build randomly fails with "*** missing separator.  Stop."
        build_parallel=False,
    ),
    Package(
        name="gnutls",
        requires=["nettle", "unistring"],
        source_url="https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/gnutls-3.8.1.tar.xz",
        build_arguments=[
            "--disable-cxx",
            "--disable-doc",
            "--disable-guile",
            "--disable-libdane",
            "--disable-nls",
            "--disable-tests",
            "--disable-tools",
            "--with-included-libtasn1",
            "--without-p11-kit",
        ],
    ),
]

codec_group = [
    Package(
        name="aom",
        requires=["cmake"],
        source_url="https://storage.googleapis.com/aom-releases/libaom-3.11.0.tar.gz",
        source_strip_components=1,
        build_system="cmake",
        build_arguments=[
            "-DENABLE_DOCS=0",
            "-DENABLE_EXAMPLES=0",
            "-DENABLE_TESTS=0",
            "-DENABLE_TOOLS=0",
        ],
        build_parallel=False,
    ),
    Package(
        name="dav1d",
        requires=["meson", "nasm", "ninja"],
        source_url="https://code.videolan.org/videolan/dav1d/-/archive/1.4.1/dav1d-1.4.1.tar.bz2",
        build_system="meson",
    ),
]

ffmpeg_package = Package(
    name="ffmpeg",
    source_url="https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz",
    build_arguments=[],
)


def download_tars(use_gnutls, stage):
    # Try to download all tars at the start.
    # If there is an curl error, do nothing, then try again in `main()`

    local_libs = library_group
    if use_gnutls:
        local_libs += gnutls_group

    if stage is None:
        the_packages = local_libs + codec_group
    elif stage == 0:
        the_packages = local_libs
    elif stage == 1:
        the_packages = codec_group
    else:
        the_packages = []

    for package in the_packages:
        tarball = os.path.join(
            os.path.abspath("source"),
            package.source_filename or package.source_url.split("/")[-1],
        )
        if not os.path.exists(tarball):
            try:
                fetch(package.source_url, tarball)
            except subprocess.CalledProcessError:
                pass


def main():
    global library_group

    parser = argparse.ArgumentParser("build-ffmpeg")
    parser.add_argument("destination")
    parser.add_argument(
        "--stage",
        default=None,
        help="AArch64 build requires stage and possible values can be 1, 2",
    )
    parser.add_argument("--enable-gpl", action="store_true")
    parser.add_argument("--disable-gpl", action="store_true")

    args = parser.parse_args()

    dest_dir = args.destination
    build_stage = None if args.stage is None else int(args.stage) - 1
    disable_gpl = args.disable_gpl
    del args

    output_dir = os.path.abspath("output")

    # FFmpeg has native TLS backends for macOS and Windows
    use_gnutls = plat == "Linux"

    if plat == "Linux" and os.environ.get("CIBUILDWHEEL") == "1":
        output_dir = "/output"
    output_tarball = os.path.join(output_dir, f"ffmpeg-{get_platform()}.tar.gz")

    if os.path.exists(output_tarball):
        return

    builder = Builder(dest_dir=dest_dir)
    builder.create_directories()

    download_tars(use_gnutls, build_stage)

    # install packages
    available_tools = set()
    if plat == "Linux" and os.environ.get("CIBUILDWHEEL") == "1":
        with log_group("install packages"):
            run(
                [
                    "yum",
                    "-y",
                    "install",
                    "gperf",
                    "libuuid-devel",
                    "libxcb-devel",
                    "zlib-devel",
                ]
            )
        available_tools.update(["gperf"])
    elif plat == "Windows":
        available_tools.update(["gperf", "nasm"])

        # print tool locations
        print("PATH", os.environ["PATH"])
        for tool in ["gcc", "g++", "curl", "gperf", "ld", "nasm", "pkg-config"]:
            run(["where", tool])

    with log_group("install python packages"):
        run(["pip", "install", "cmake", "meson", "ninja"])

    # build tools
    if "gperf" not in available_tools:
        builder.build(
            Package(
                name="gperf",
                source_url="http://ftp.gnu.org/pub/gnu/gperf/gperf-3.1.tar.gz",
            ),
            for_builder=True,
        )

    if "nasm" not in available_tools:
        builder.build(
            Package(
                name="nasm",
                source_url="https://www.nasm.us/pub/nasm/releasebuilds/2.14.02/nasm-2.14.02.tar.bz2",
            ),
            for_builder=True,
        )

    ffmpeg_package.build_arguments = [
        "--disable-alsa",
        "--disable-doc",
        "--disable-libtheora",
        "--disable-libfreetype",
        "--disable-libfontconfig",
        "--disable-libbluray",
        "--disable-libopenjpeg",
        (
            "--enable-mediafoundation"
            if plat == "Windows"
            else "--disable-mediafoundation"
        ),
        "--enable-gmp",
        "--enable-gnutls" if use_gnutls else "--disable-gnutls",
        "--enable-libaom",
        "--enable-libdav1d",
        "--enable-libmp3lame",

        "--enable-libxcb" if plat == "Linux" else "--disable-libxcb",
        "--enable-libxml2",
        "--enable-lzma",
        "--enable-zlib",
        "--enable-version3",
        "--disable-libopenh264"
    ]


    if plat == "Darwin":
        ffmpeg_package.build_arguments.extend(
            ["--enable-videotoolbox", "--extra-ldflags=-Wl,-ld_classic"]
        )

    if use_gnutls:
        library_group += gnutls_group

    package_groups = [library_group + codec_group, [ffmpeg_package]]
    if build_stage is not None:
        packages = package_groups[build_stage]
    else:
        packages = [p for p_list in package_groups for p in p_list]

    for package in packages:
        if package.name == "ffmpeg":
            print('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
            os.environ['PKG_CONFIG_PATH'] = f"/c/cibw/vendor/lib/pkgconfig:{os.environ['PKG_CONFIG_PATH']}"
            print(subprocess.run(['pkg-config', '--modversion', 'aom'], shell=True, env=os.environ))
            print('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')
            subprocess.run(["ls", "-al", "/c/cibw/vendor/lib/pkgconfig"])
            print('ccccccccccccccccccccccccccccccccccccc')
        if disable_gpl and package.gpl:
            if package.name == "x264":
                builder.build(openh264)
            else:
                pass
        else:
            builder.build(package)

    if plat == "Windows" and (build_stage is None or build_stage == 1):
        # fix .lib files being installed in the wrong directory
        for name in (
            "avcodec",
            "avdevice",
            "avfilter",
            "avformat",
            "avutil",
            "postproc",
            "swresample",
            "swscale",
        ):
            if os.path.exists(os.path.join(dest_dir, "bin", name + ".lib")):
                shutil.move(
                    os.path.join(dest_dir, "bin", name + ".lib"),
                    os.path.join(dest_dir, "lib"),
                )

        os.makedirs("C:\\cibw\\vendor\\bin\\include\\lib")

        # copy some libraries provided by mingw
        mingw_bindir = os.path.dirname(
            subprocess.run(["where", "gcc"], check=True, stdout=subprocess.PIPE)
            .stdout.decode()
            .splitlines()[0]
            .strip()
        )
        for name in (
            "libgcc_s_seh-1.dll",
            "libiconv-2.dll",
            "libstdc++-6.dll",
            "libwinpthread-1.dll",
            "zlib1.dll",
        ):
            shutil.copy(os.path.join(mingw_bindir, name), os.path.join(dest_dir, "bin"))

    # find libraries
    if plat == "Darwin":
        libraries = glob.glob(os.path.join(dest_dir, "lib", "*.dylib"))
    elif plat == "Linux":
        libraries = glob.glob(os.path.join(dest_dir, "lib", "*.so"))
    elif plat == "Windows":
        libraries = glob.glob(os.path.join(dest_dir, "bin", "*.dll"))

    
    # build output tarball
    if build_stage is None or build_stage == 1:
        os.makedirs(output_dir, exist_ok=True)
        run(["tar", "czvf", output_tarball, "-C", dest_dir, "bin", "include", "lib"])


if __name__ == "__main__":
    main()
    
    
