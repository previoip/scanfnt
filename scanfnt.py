import os
import sys
import struct
from io import BytesIO
from collections import deque, namedtuple

# TrueType Font File (TTFF) Specification
# https://developer.apple.com/fonts/TrueType-Reference-Manual/RM06/Chap6.html

# OpenType Font File (OTFF) Specification
# https://learn.microsoft.com/en-us/typography/opentype/spec/otff


class SIG:
  ttf_0100 = b'\x00\x01\x00\x00'
  ttf_true = b'\x74\x72\x75\x65'
  ttf_typ1 = b'\x74\x79\x70\x31'
  otf_otto = b'\x4F\x54\x54\x4F'
  otf_tccf = b'tccf'


otf_descriptor_fmt = '!4sHHHH'
table_record_fmt = '!4sIII'
tccf_descriptor_v1_fmt = '!4sHHI'




t_file_info = namedtuple('FileInfo', ['size', 'stat'])
t_otf_sfnt = namedtuple('sfnt', ['sfntVersion', 'numTables', 'searchRange', 'entrySelector', 'rangeShift'])
t_otf_record = namedtuple('record', ['tag', 'checksum', 'offset', 'length'])

def get_file_info(path):
  return t_file_info(
    os.path.getsize(path),
    os.stat(path)
  )

def read_unpack(fp, fmt):
  return struct.unpack(fmt, fp.read(struct.calcsize(fmt)))


def calc_table_checksum(fp, offset, length):
  s = 0
  p = fp.tell()
  fp.seek(offset)
  n, t = divmod(length, 4)
  for i in range(n):
    s += struct.unpack('!I', fp.read(4).ljust(4, b'\x00'))[0]
  if t > 0:
    s += struct.unpack('!I', fp.read(4-t).ljust(4, b'\x00'))[0]
  fp.seek(p)
  return s

if __name__ == '__main__':
  if len(sys.argv) == 1:
    exit()

  offsets = list()

  filepath = sys.argv[1]
  fileinfo = get_file_info(filepath)
  if not fileinfo.stat.st_mode & os.R_OK: 
    raise OSError('invalid file permission: not readable')

  fp = open(filepath, 'rb')
  fp.seek(0)

  buf = b''
  print('scanning')
  while True:
    b = fp.read(1)
    if not b: break
    buf += b
    buf = buf[-4:]

    if buf.endswith((
      SIG.ttf_0100,
      SIG.ttf_true,
      SIG.ttf_typ1,
      SIG.otf_otto,
      SIG.otf_tccf
    )): 
      if buf.endswith(SIG.otf_tccf):
        print('found collections')
      else:
        offsets.append(fp.tell()-4)

  print('found {} location candidates'.format(len(offsets)))
  print()

  for n, o in enumerate(offsets):
    fp.seek(o)
    sfnt = t_otf_sfnt(*read_unpack(fp, otf_descriptor_fmt))
    if sfnt.numTables == 0:
      continue
    if not sfnt.rangeShift == sfnt.numTables * 16 - sfnt.searchRange:
      continue

    print(f'found valid offset ({n}):', sfnt)

    for i in range(sfnt.numTables):
      record = t_otf_record(*read_unpack(fp, table_record_fmt))
      checksum = calc_table_checksum(fp, record.offset, record.length)
      print(f'{i:3d}', record, 'checksum match:', checksum == record.checksum, checksum)
    print()
  fp.close()