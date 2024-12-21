import os
import sys
import struct
from io import BytesIO
from re import compile as re_compile
from collections import deque, namedtuple

# TrueType Font File (TTFF) Specification
# https://developer.apple.com/fonts/TrueType-Reference-Manual/RM06/Chap6.html

# OpenType Font File (OTFF) Specification
# https://learn.microsoft.com/en-us/typography/opentype/spec/otff

EXPORT_FOLDER = './exports'

class SIG:
  ttf_0100 = b'\x00\x01\x00\x00'
  ttf_true = b'\x74\x72\x75\x65'
  ttf_typ1 = b'\x74\x79\x70\x31'
  otf_otto = b'\x4F\x54\x54\x4F'
  otf_tccf = b'tccf'

class struct_fmt:
  otf_descriptor = '!4sHHHH'
  table_record = '!4sIII'
  tccf_descriptor_v1 = '!4sHHI'
  otf_table_name_header = '!3H'
  otf_table_name_records = '!6H'

regexp_invalid_pathstr = re_compile(r'[<>:"/\|?*\x00]')

t_file_info = namedtuple('FileInfo', ['size', 'stat'])
t_target_chunk = namedtuple('TargetChunk', ['filename', 'offset', 'length'])
t_otf_sfnt = namedtuple('sfnt', ['sfntVersion', 'numTables', 'searchRange', 'entrySelector', 'rangeShift'])
t_otf_record = namedtuple('record', ['tag', 'checksum', 'offset', 'length'])
t_otf_table_name_header = namedtuple('table_name', ['version', 'count', 'storageOffset'])
t_otf_table_name_records = namedtuple('table_name_records', ['platformId', 'encodingId', 'languageId', 'nameId', 'length', 'offset'])

def get_file_info(path):
  return t_file_info(
    os.path.getsize(path),
    os.stat(path)
  )

def read_unpack(fp, fmt):
  return struct.unpack(fmt, fp.read(struct.calcsize(fmt)))

def calc_table_checksum(fp, offset, length, bufsize=2**10):
  s = 0
  p = fp.tell()
  fp.seek(offset)
  while length > 0:
    size = bufsize if length > bufsize else length
    buf = fp.read(size)
    n, left = divmod(size, 4)
    if left > 0:
      n += 1
      buf = b'\x00' * (4-left) + buf
    s += sum(struct.unpack(f'!{n}I', buf))
    length -= bufsize
    if length < 0:
      break
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

  os.makedirs(EXPORT_FOLDER, exist_ok=True)
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
        print('found collections, skipped')
      else:
        offsets.append(fp.tell()-4)

  print('found {} location candidates'.format(len(offsets)))
  print()

  to_save = list()
  for n, base_offset in enumerate(offsets):
    do_save = False
    has_prefix = False
    filename = ''
    total_length = 0
    fp.seek(base_offset)
    if base_offset + 12 > fileinfo.size:
      continue
    sfnt = t_otf_sfnt(*read_unpack(fp, struct_fmt.otf_descriptor))
    if sfnt.numTables == 0:
      continue
    if sfnt.rangeShift != sfnt.numTables * 16 - sfnt.searchRange:
      continue

    print(f'found valid offset ({n}):', sfnt)

    for i in range(sfnt.numTables):
      record = t_otf_record(*read_unpack(fp, struct_fmt.table_record))
      # checksum = calc_table_checksum(fp, base_offset + record.offset, record.length)
      # is_valid_record = checksum == record.checksum
      print(f'{i:3d}', record)
      total_length = max(total_length, record.offset + record.length)
      if record.tag == b'name':
        print('    found name table')
        p0 = fp.tell()
        fp.seek(base_offset + record.offset)
        name_record_header = t_otf_table_name_header(*read_unpack(fp, struct_fmt.otf_table_name_header))
        print('   ', name_record_header)
        for _ in range(name_record_header.count):
          name_records = t_otf_table_name_records(*read_unpack(fp, struct_fmt.otf_table_name_records))
          # print(name_records)
          p1 = fp.tell()
          fp.seek(base_offset + record.offset + name_record_header.storageOffset + name_records.offset)
          name_bytes, = read_unpack(fp, f'!{name_records.length}s')
          name_string = name_bytes.decode('utf8')
          if name_records.nameId == 0:
            pass
          print(f'     {name_records.encodingId} {name_records.platformId} {name_records.nameId:2d}', name_string)
          if not do_save and name_records.nameId == 1:
            do_save = True
            filename = name_string
          if do_save and not has_prefix and name_records.nameId == 2:
            has_prefix = True
            filename += ' - ' + name_string
          fp.seek(p1)
        fp.seek(p0)
    print()

    if do_save:
      extension = '.ttf' if sfnt.sfntVersion in (SIG.ttf_true, SIG.ttf_typ1, SIG.ttf_0100) else '.otf'
      filename += extension
      filename = regexp_invalid_pathstr.sub('', filename)
      to_save.append(t_target_chunk(filename, base_offset, total_length))

  for info in to_save:
    print('saving', info.filename)
    fp.seek(info.offset)
    with open(os.path.join(EXPORT_FOLDER, info.filename), 'wb') as fp_e:
      fp_e.write(fp.read(info.length))
  fp.close()