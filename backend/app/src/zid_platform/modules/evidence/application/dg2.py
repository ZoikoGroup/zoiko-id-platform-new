"""The face image inside data group 2 of a passport or eID chip (ICAO Doc 9303 part 10; CR-102).

DG2 wraps one or more biometric templates in nested TLV. Each holds a facial image in JPEG or JPEG 2000. This finds the first image by its file
signature inside the data group, bounded in size, and returns its bytes. It does not decode the image, judge its quality or read the template's
other fields. The caller may use the image only when data group 2 was one of the groups the signed object vouched for (`data_groups_verified`
from `epassport.verify`): a photo from an unverified group is not a chip photo and never binds anyone.
"""

from __future__ import annotations

MAX_IMAGE = 1024 * 1024
_SIGNATURES = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x00\x00\x00\x0cjP  \r\n\x87\n", "JPEG2000"),
    (b"\xff\x4f\xff\x51", "JPEG2000"),
)


def chip_photo(dg2: bytes) -> bytes | None:
    """The first facial image in the data group, or None. Never raises."""
    if not isinstance(dg2, bytes | bytearray) or len(dg2) < 16 or len(dg2) > 2 * MAX_IMAGE:
        return None
    start = None
    for sig, _kind in _SIGNATURES:
        at = bytes(dg2).find(sig)
        if at != -1 and (start is None or at < start):
            start = at
    if start is None:
        return None
    image = bytes(dg2[start:])
    return image if 0 < len(image) <= MAX_IMAGE else None
