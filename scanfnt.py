import os
import sys
import struct
import time
from re import compile as re_compile
from collections import deque, namedtuple
from hashlib import sha1

# TrueType Font File (TTFF) Specification
# https://developer.apple.com/fonts/TrueType-Reference-Manual/RM06/Chap6.html

# OpenType Font File (OTFF) Specification
# https://learn.microsoft.com/en-us/typography/opentype/spec/otff

EXPORT_FOLDER = './exports'
SAVE_INVALID = False
BUF_SIZE = 2**16
BUF_PAD = 4

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
  otf_table_name_header = '!HHH'
  otf_table_name_records = '!HHHHHH'

regexp_invalid_pathstr = re_compile(r'[<>:"/\|?*\x00]')

t_target_chunk = namedtuple('TargetChunk', ['filename', 'offset', 'length'])
t_otf_sfnt = namedtuple('sfnt', ['sfntVersion', 'numTables', 'searchRange', 'entrySelector', 'rangeShift'])
t_otf_record = namedtuple('record', ['tag', 'checksum', 'offset', 'length'])
t_otf_table_name_header = namedtuple('table_name', ['version', 'count', 'storageOffset'])
t_otf_table_name_records = namedtuple('table_name_records', ['platformId', 'encodingId', 'languageId', 'nameId', 'length', 'offset'])

d_table_name_id = {
  0: 'copyright',         10: 'description',        20: 'postscript CID',
  1: 'font family',       11: 'vendor url',         21: 'WWS family',
  2: 'font subfamily',    12: 'designer url',       22: 'WWS subfamily',
  3: 'identifier',        13: 'license',            23: 'light background pallette',
  4: 'full font name',    14: 'license url',        24: 'dark background pallette',
  5: 'version',           15: 'reserved',           25: 'postscript variation prefix',
  6: 'postscript name',   16: 'typographic family',
  7: 'trademark',         17: 'typographic subfamily',
  8: 'manufacturer',      18: 'mac compat',
  9: 'designer',          19: 'sample text',
}

def get_file_info(path):
  t_file_info = namedtuple('FileInfo', ['size', 'stat'])
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

def iter_progress(it, total, prefix='', suffix=''):
  t0 = time.time()
  tt = t0
  for n, i in enumerate(it):
    n += 1
    t = time.time()
    if t - tt > .1:
      dt = int(t-t0)
      m, s = divmod(dt, 20)
      sys.stdout.write(f'{prefix}{n}/{total}{suffix} elapsed:{time.strftime("%H:%M:%S", t-t0)}s\r')
      sys.stdout.flush()
      tt = t
    yield i
  sys.stdout.write('\n')
  sys.stdout.flush()


if __name__ == '__main__':
  if len(sys.argv) == 1:
    exit()

  filepath = sys.argv[1]
  fileinfo = get_file_info(filepath)
  if not fileinfo.stat.st_mode & os.R_OK: 
    raise OSError('invalid file permission: not readable')

  os.makedirs(EXPORT_FOLDER, exist_ok=True)
  fp = open(filepath, 'rb')

  chunk_nums, left = divmod(fileinfo.size, BUF_SIZE)
  if left != 0:
    chunk_nums += 1
  chunk_nums = max(1, chunk_nums)

  buf = bytearray(BUF_SIZE+BUF_PAD)
  loop = True
  offsets = list()
  chunk_offset = -BUF_PAD

  for _ in iter_progress(range(chunk_nums), chunk_nums, 'scanning ', ' chunks'):

    for i in range(BUF_PAD):
      buf[i] = buf[BUF_PAD+i]
    for i, b in enumerate(fp.read(BUF_SIZE)):
      buf[BUF_PAD+i] = b
      if i+1 == BUF_SIZE: break
    else:
      for j in range(BUF_SIZE-i):
        buf[BUF_SIZE+BUF_PAD-j-1] = 0
      loop = False
    
    for i in range(BUF_SIZE):
      sample = buf[i:i+4]
      if sample in (
          SIG.ttf_0100,
          SIG.ttf_true,
          SIG.ttf_typ1,
          SIG.otf_otto,
        ):
        offsets.append(chunk_offset + i)
      if sample == SIG.otf_tccf:
        print('found collections at {}, skipping'.format(chunk_offset))
    chunk_offset += BUF_SIZE

  print('found {} offset candidates'.format(len(offsets)))
  print()

  to_save = list()
  for n, base_offset in enumerate(offsets):
    has_name = False
    has_subfamily = False
    filename = ''
    total_length = 0
    if base_offset + 12 > fileinfo.size: continue

    fp.seek(base_offset)
    sfnt = t_otf_sfnt(*read_unpack(fp, struct_fmt.otf_descriptor))
    if sfnt.numTables == 0: continue
    if sfnt.rangeShift == 0: continue
    if sfnt.searchRange == 0: continue
    if sfnt.numTables * 16 - sfnt.searchRange != sfnt.rangeShift: continue

    print(f'found valid offset ({hex(base_offset)}):', sfnt)

    for i in range(sfnt.numTables):
      record = t_otf_record(*read_unpack(fp, struct_fmt.table_record))

      # checksum = calc_table_checksum(fp, base_offset + record.offset, record.length)
      # is_valid_record = checksum == record.checksum
      # print(f'{i:3d}', record)
      total_length = max(total_length, record.offset + record.length)

      if record.tag == b'name':
        p0 = fp.tell()
        fp.seek(base_offset + record.offset)
        name_record_header = t_otf_table_name_header(*read_unpack(fp, struct_fmt.otf_table_name_header))
        print(f'found name table ({name_record_header})')
        print()
        for _ in range(name_record_header.count):
          name_records = t_otf_table_name_records(*read_unpack(fp, struct_fmt.otf_table_name_records))
          # print(name_records)
          p1 = fp.tell()
          fp.seek(base_offset + record.offset + name_record_header.storageOffset + name_records.offset)
          name_bytes, = read_unpack(fp, f'!{name_records.length}s')
          name_string = name_bytes.decode('utf8')
          if name_records.nameId == 0:
            pass
          print(' >',d_table_name_id.get(name_records.nameId, 'undefined'), f'(id:{name_records.nameId} platform:{name_records.platformId} encoding:{name_records.encodingId}) :')
          print('  ', f'"{name_string}"')
          if not has_name and name_records.nameId == 1:
            has_name = True
            filename = name_string
          if has_name and not has_subfamily and name_records.nameId == 2:
            has_subfamily = True
            filename += ' - ' + name_string
          fp.seek(p1)
        fp.seek(p0)
    print()

    if not has_name and not SAVE_INVALID: continue
    if not has_name:
      sha = sha1()
      p = fp.tell()
      fp.seek(base_offset)
      for i in range(total_length // 1024):
        sha.update(fp.read(1024))
      fp.seek(p)
      filename = sha.hexdigest()

    extension = '.ttf' if sfnt.sfntVersion in (SIG.ttf_true, SIG.ttf_typ1, SIG.ttf_0100) else '.otf'
    filename += extension
    filename = regexp_invalid_pathstr.sub('', filename)
    to_save.append(t_target_chunk(filename, base_offset, total_length))

  if len(to_save) == 0:
    print('no valid font is found')

  for info in to_save:
    print('saving', f'"{info.filename}"')
    fp.seek(info.offset)
    with open(os.path.join(EXPORT_FOLDER, info.filename), 'wb') as fp_e:
      fp_e.write(fp.read(info.length))

  fp.close()
