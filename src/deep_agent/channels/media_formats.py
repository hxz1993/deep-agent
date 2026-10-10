"""Content-based media format checks shared by tool and transport boundaries."""

def is_mp4_video(data: bytes) -> bool:
    """Recognize an MP4 container with a video track, without decoding codecs.

    `ftyp` is shared by QuickTime, AVIF and audio-only M4A. A matching byte
    sequence alone is not enough to select a native video upload.
    """
    remaining_boxes = 4096

    def boxes(start: int, end: int):
        nonlocal remaining_boxes
        while start < end:
            remaining_boxes -= 1
            if remaining_boxes < 0 or end - start < 8:
                raise ValueError("Invalid BMFF box")
            size = int.from_bytes(data[start:start + 4], "big")
            kind = data[start + 4:start + 8]
            header = 8
            if size == 1:
                if end - start < 16:
                    raise ValueError("Invalid extended BMFF box")
                size = int.from_bytes(data[start + 8:start + 16], "big")
                header = 16
            elif size == 0:
                size = end - start
            if size < header or size > end - start:
                raise ValueError("Invalid BMFF box bounds")
            yield kind, start + header, start + size
            start += size

    mp4_brands = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"iso7",
                  b"iso8", b"iso9", b"mp41", b"mp42", b"avc1", b"dash", b"M4V "}
    has_mp4_brand = False
    has_video_track = False
    try:
        for kind, start, end in boxes(0, len(data)):
            if kind == b"ftyp":
                if end - start < 8 or (end - start - 8) % 4:
                    return False
                major = data[start:start + 4]
                if major in {b"qt  ", b"avif", b"avis", b"heic", b"heix", b"mif1", b"msf1"}:
                    return False
                brands = {major, *(data[i:i + 4] for i in range(start + 8, end, 4))}
                has_mp4_brand = bool(brands & mp4_brands)
            elif kind == b"moov":
                for child, track_start, track_end in boxes(start, end):
                    if child != b"trak":
                        continue
                    for track_child, media_start, media_end in boxes(track_start, track_end):
                        if track_child != b"mdia":
                            continue
                        for media_child, handler_start, handler_end in boxes(media_start, media_end):
                            if media_child == b"hdlr" and handler_end - handler_start >= 12:
                                # FullBox flags/version, pre_defined, handler_type.
                                has_video_track |= data[handler_start + 8:handler_start + 12] == b"vide"
    except ValueError:
        return False
    return has_mp4_brand and has_video_track

