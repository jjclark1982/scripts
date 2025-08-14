#!/usr/bin/env python3

# Date Scraper
# use cases:
# - read all dates of a file
# - set mtime based on metadata or filename
# - add date to filename
# - add index to filename based on date order


# Download video with upload_date in filename:
# yt-dlp -f mp4 -o "%(title)+.100U (%(upload_date>%Y-%m-%d)s) [%(id)s].%(ext)s" "$URL"

# Read video metadata
# ffprobe -v quiet -print_format json -show_format -show_streams input.m4v


from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import dateutil.parser
from functools import cached_property
import glob
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
from pathlib import Path
import uuid

from PIL import Image
import PIL.ExifTags as ExifTags
try:
    from pillow_heif import register_heif_opener    # HEIF support
    register_heif_opener()                          # HEIF support
    import pillow_avif                              # AVIF support
except ModuleNotFoundError:
    pass


@dataclass
class FileDateInfo:
    """
    Lazy-loading interface to scraping and parsing various file dates.
    """
    path: Path

    @cached_property
    def dates(self):
        dates = {}
        dates.update(get_dates_from_filename(self.path))
        dates.update(get_dates_from_filesystem(self.path))
        dates.update(get_dates_from_xattr(self.path))
        dates.update(get_dates_from_media(self.path))
        dates.update(get_dates_from_exif(self.path))
        return dates

    @cached_property
    def earliest_date(self):
        return min_valid_date(self.dates.values())

    def set_mtime_to_earliest(self):
        set_filesystem_times(
            self.path,
            atime=self.dates['File Accessed'],
            mtime=self.earliest_date
        )

    def __str__(self):
        lines = [str(self.path)]
        for name in sorted(self.dates, key=self.dates.get):
            date = self.dates[name]
            lines.append(f"{name}: {date}{' (earliest)' if date == self.earliest_date else ''}")
        return "\n".join(lines)

    def pretty_str(self):
        lines = [f"\033[1m{self.path}\033[0;0m"]
        for name in sorted(self.dates, key=self.dates.get):
            date = self.dates[name]
            if date == self.earliest_date:
                lines.append(f"{name}: \033[4m{date}\033[0;0m")
            else:
                lines.append(f"{name}: {date}")
        return "\n".join(lines)


def is_date_plausible(date):
    if date is None:
        return False
    too_old = datetime(1971, 1, 2, tzinfo=timezone.utc)
    too_new = datetime.now().astimezone() + timedelta(days=365*5)
    return (too_old < date < too_new)


def min_valid_date(dates):
    valid_dates = sorted([d for d in dates if is_date_plausible(d)])
    if len(valid_dates) == 0:
        return None

    # if the minimum date is a truncated version of a more precise timestamp, return the more precise one
    precise_times = [d for d in valid_dates if d.microsecond != 0]
    if (len(precise_times) > 0) and (precise_times[0] - valid_dates[0] < timedelta(minutes=1)):
        return precise_times[0]

    precise_dates = [d for d in valid_dates if d.hour != 0 or d.minute != 0 or d.second != 0]
    if (len(precise_dates) > 0) and (precise_dates[0] - valid_dates[0] < timedelta(days=1)):
        return precise_dates[0]

    return valid_dates[0]


def parse_date_from_timestamp(s):
    # timestamps in filenames were not common before 10-digit numbers (ts 1_000_000_000 = Sep 2001)
    # but could be plausible for some 9-digit numbers (ts 500_000_000 = Nov 1985)

    # detect 13-digit timestamp with millisecond precision (e.g. from JavaScript)
    match = re.search(r"\b1\d{12}\b", s)
    if match:
        timestamp = int(match[0]) / 1000.0
        date = datetime.fromtimestamp(timestamp).astimezone(timezone.utc)
        return date

    # detect 10-digit timestamp with second precision (e.g. from imageboard uploads)
    match = re.search(r"\b1\d{9}\b", s)
    if match:
        timestamp = int(match[0])
        date = datetime.fromtimestamp(timestamp).astimezone(timezone.utc)
        return date

    return None


def parse_date_from_uuid(s):
    # detect timestamp in UUID v1
    uuid_re = r"(?i:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    match = re.search(uuid_re, s)
    if match:
        id = uuid.UUID(match[0])
        if id.version == 1:
            date = datetime.fromtimestamp((id.time - 0x01b21dd213814000) * 100 / 1e9).astimezone(timezone.utc)
            return date

    return None


def parse_date_from_text(text):
    # detect full year in parentheses
    match = re.search(r"(?:\(|^)((?:19|20)\d{2}(?:[\s.:/-]\d{1,2}){0,5})(?:\)|$|\.)", text)
    if match:
        fields = [*map(int, re.split(r"\D", match[1]))]
        if len(fields) == 6:
            # YYYY-MM-DD HH:MM:SS detected
            return datetime(*fields, tzinfo=timezone.utc)
        elif len(fields) == 3:
            # YYYY-MM-DD detected
            return datetime(*fields, tzinfo=timezone.utc)
        elif len(fields) == 2:
            # YYYY-MM detected - mark as the last day of the month
            return datetime(fields[0], fields[1] + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
        elif len(fields) == 1:
            # YYYY detected - mark as the last day of the year
            return datetime(fields[0] + 1, 1, 1, tzinfo=timezone.utc) - timedelta(days=1)

    # try to detect any date (watch out for false positives)
    match = re.search(r"\b(?:19|20)\d{2}[\d./-]*\b", text)
    if match:
        try:
            date = dateutil.parser.parse(match[0], fuzzy=False, default=datetime(1970,1,1, tzinfo=timezone.utc))
            if is_date_plausible(date):
                return date
        except dateutil.parser.ParserError:
            pass

    # try:
    #     date = dateutil.parser.parse(text, fuzzy=True, default=datetime(1970,1,1, tzinfo=timezone.utc))
    #     if is_date_plausible(date):
    #         return date
    # except dateutil.parser.ParserError:
    #     pass


    return None


def get_dates_from_filename(filename):
    names = {
        "Filename": Path(filename).stem,
        "Folder name": Path(filename).resolve().parent.name
    }
    date_parsers = {
        "Timestamp": parse_date_from_timestamp,
        "UUID": parse_date_from_uuid,
        "Date": parse_date_from_text
    }
    dates = {}
    for (name_type, name) in names.items():
        for (date_type, parser) in date_parsers.items():
            date = parser(name)
            if date is not None:
                dates[f"{date_type} in {name_type}"] = date

    return dates


def get_dates_from_filesystem(filename):
    dates = {}
    stats = os.stat(filename)
    dates['File Accessed'] = datetime.fromtimestamp(stats.st_atime).astimezone(timezone.utc)
    dates['File Modified'] = datetime.fromtimestamp(stats.st_mtime).astimezone(timezone.utc)
    dates['File Changed'] = datetime.fromtimestamp(stats.st_ctime).astimezone(timezone.utc)

    if shutil.which('GetFileInfo'):
        creation_time = subprocess.run(['GetFileInfo', '-d', filename], capture_output=True).stdout
        dates['File Created'] = dateutil.parser.parse(creation_time).astimezone(timezone.utc)
    return dates


def set_filesystem_times(filename, atime=None, mtime=None, creation_time=None):
    if atime is None:
        atime = datetime.now()
    if mtime:
        os.utime(filename, times=(atime.timestamp(), mtime.timestamp()))
    if creation_time:
        if shutil.which('SetFile'):
            date = creation_time
            date_str = f"{date.month}/{date.day}/{date.year} {date.hour}:{date.minute}:{date.second}"
            subprocess.run(['SetFile', '-d', date_str, filename])


def get_dates_from_xattr(filename):
    dates = {}
    if shutil.which('xattr'):
        xattr_names = subprocess.run(['xattr', str(filename)], capture_output=True).stdout.decode().strip().split("\n")
        for xattr_name in xattr_names:
            match = re.search(r"(?i:date|time)", xattr_name)
            if match:
                xattr_value = subprocess.run(['xattr', '-p', xattr_name, str(filename)], capture_output=True).stdout
                try:
                    date = parse_date_from_text(xattr_value.decode())
                    if date is not None:
                        dates[f"Xattr {xattr_name}"] = date
                except UnicodeDecodeError:
                    # unknown binary data format in xattr value
                    pass
    return dates


def get_dates_from_media(filename):
    dates = {}
    if shutil.which('ffprobe'):
        ffprobe_result = subprocess.run([
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            filename
        ], capture_output=True)
        metadata = json.loads(ffprobe_result.stdout)
        if 'format' in metadata:
            creation_time = metadata['format'].get('creation_time')
            if creation_time:
                dates["Media Format Created"] = dateutil.parser.parse(creation_time)
        for stream in metadata.get('streams', []):
            if stream.get('tags'):
                creation_time = stream['tags'].get('creation_time')
                if creation_time:
                    name = f"{stream['codec_type'].capitalize()} Stream {stream['index']} Created"
                    dates[name] = dateutil.parser.parse(creation_time)
    return dates


def get_dates_from_exif(filename):
    dates = {}

    try:
        img = Image.open(filename)
        exif = img.getexif()
    except Image.UnidentifiedImageError:
        return dates

    # ExifTags.Base.TimeZoneOffset is not widely supported

    d = exif.get(ExifTags.Base.DateTime)
    if d:
        dates["Exif DateTime"] = parse_date_from_text(d)

    ifd_tags = [
        ExifTags.Base.DateTimeOriginal,
        ExifTags.Base.DateTimeDigitized,
        ExifTags.Base.PreviewDateTime
    ]
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    for tag in ifd_tags:
        d = exif_ifd.get(tag)
        if d:
            dates[f"Exif {tag.name}"] = parse_date_from_text(d)

    gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
    d = gps_ifd.get(ExifTags.GPS.GPSDateStamp)
    t = gps_ifd.get(ExifTags.GPS.GPSTimeStamp)
    if d:
        dates["Exif GPSDateStamp"] = parse_date_from_text(' '.join([d or '', t or '']))

    return dates


class DateScraper:
    """
    Utility to read dates from various types of file metadata,
    and write dates to file creation and modification time.
    """

    @classmethod
    def main_cli(cls):
        import argparse

        parser = argparse.ArgumentParser(
            # prog=cls.__name__,
            description=cls.__doc__
        )
        parser.add_argument('paths', nargs='+', type=Path, help='paths to process')
        parser.add_argument('--rewrite-mtime', action='store_true', help='paths to process')
        parser.add_argument('--verbose', action='store_true', default=True)
        parser.add_argument('--quiet', dest='verbose', action='store_false')
        args = parser.parse_args()

        scraper = DateScraper(*args.paths, **args.__dict__)
        scraper.run(**args.__dict__)

    def __init__(self, *paths, verbose=False, **kwargs):
        all_paths = expand_paths(paths)
        self.files = map(FileDateInfo, all_paths)
        self.n_files = len(all_paths)

    def run(self, verbose=False, rewrite_mtime=False, **kwargs):
        if verbose and self.n_files > 1:
            print(f"{self.__class__.__name__}: Reading {self.n_files} files...\n")

        n_written = 0
        for file_info in self.files:
            if verbose:
                if sys.stdout.isatty():
                    print(file_info.pretty_str())
                else:
                    print(file_info)

            if rewrite_mtime:
                prev_mtime = file_info.dates['File Modified']
                if prev_mtime != file_info.earliest_date:
                    file_info.set_mtime_to_earliest()
                    print(f"Changed modification time of {shlex.quote(str(file_info.path))} from '{prev_mtime}' to '{file_info.earliest_date}'")
                    n_written += 1

            if verbose:
                print()

        if verbose and n_written > 0:
            print(f"{self.__class__.__name__}: Updated {n_written} / {self.n_files} files.")


def expand_paths(paths):
    results = set()
    for path in paths:
        if path.is_dir():
            results.update(map(Path, glob.iglob(str(path / "**/*"), recursive=True)))
        elif path.exists():
            results.add(path)
        else:
            results.update(map(Path, glob.iglob(str(path), recursive=True)))

    return [p for p in sorted(results) if not p.is_dir()]


if __name__ == "__main__":
    DateScraper.main_cli()


def test_get_date_from_text():
    assert get_date_from_text("abc (2025-02-03) xyz ") == datetime(2025, 2, 3, tzinfo=timezone.utc)
    assert get_date_from_text("abc (2025-02) xyz    ") == datetime(2025, 2, 28, tzinfo=timezone.utc)
    assert get_date_from_text("abc (2025) xyz       ") == datetime(2025, 12, 31, tzinfo=timezone.utc)
    assert get_date_from_text("abc.2025.04.05.xyz   ") == datetime(2025, 4, 5, tzinfo=timezone.utc)
    assert get_date_from_text("abc/2025/04/06/01.xyz") == datetime(2025, 4, 6, tzinfo=timezone.utc)
    assert get_date_from_text("abc 1753386545083 xyz") == datetime(2025, 7, 24, 19, 49, 5, 83000, tzinfo=timezone.utc)
    assert get_date_from_text("abc 1753386545 xyz   ") == datetime(2025, 7, 24, 19, 49, 5, tzinfo=timezone.utc)
    assert get_date_from_text("abc ae4e6160-68c2-11f0-b558-1800200c9a66 xyz") == datetime(2025, 7, 24, 19, 16, 20, 580183, tzinfo=timezone.utc)
    assert get_date_from_text("Photo May 19 2023, 11 59 04 PM.jpg") == datetime(2023, 5, 19, 23, 59, 4, tzinfo=timezone.utc)
    "Screenshot 20200427 220511.jpg"
    "RDT 20221123 2347021999855542208290714.webp"
    "1308903232501100548 1.jpg"  # looks like a nanosecond timestamp but may be just a coincidence
