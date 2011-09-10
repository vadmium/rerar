#! /usr/bin/env python3

USE_NUMPY = True
USE_ARRAY = True

# Optimum values possibly depend on speed of Python and cache sizes and
# characteristics
buf_size = 0x10000
FILE_CRC_BUF = buf_size
FILE_COPY_CRC_BUF = buf_size

from rarfile import (
    Struct, crc32,
    S_BLK_HDR, S_FILE_HDR, S_SHORT, S_LONG,
    RAR_ID,
    RAR_BLOCK_MAIN, RAR_BLOCK_FILE, RAR_BLOCK_SUB, RAR_BLOCK_ENDARC,
    RAR_LONG_BLOCK, RAR_SKIP_IF_UNKNOWN,
    RAR_MAIN_VOLUME, RAR_MAIN_RECOVERY, RAR_MAIN_FIRSTVOLUME,
    RAR_FILE_SPLIT_BEFORE, RAR_FILE_SPLIT_AFTER, RAR_FILE_DICT4096,
    RAR_FILE_UNICODE, RAR_FILE_EXTTIME,
    RAR_ENDARC_NEXT_VOLUME, RAR_ENDARC_DATACRC, RAR_ENDARC_REVSPACE,
    RAR_ENDARC_VOLNR,
    RAR_OS_WIN32,
)

import sys
import os
import time
import re
import struct
import io
import math

if USE_NUMPY:
    try: import numpy
    except ImportError: USE_NUMPY = False
if not USE_NUMPY and USE_ARRAY: import array

def main():
    # size 15M => Rar volume size. M specifies units of 10^6. Default is 15M. (TODO: Use scene rules for default.)
    # srr => Produce rescene file rather than Rar files
    # srr-rr-full => Do not strip Rar recovery records (older SRR file format). Requires srr and one of the rr options.
    # rr => Produce Rar 3 recovery records
    # rr-old => Produce older Rar recovery records
    # internal name-grp.avi => Name to be used for the data file inside the Rar set. Defaults to the external file name, without any directory components.
    # volume => Explicitly specify first, second, etc full volume name. Default is ".partN.rar" for "new" Rar 3 naming scheme, where the number of digits is automatically determined by the total number of volumes; and ".rar", ".r00", ".r01", etc, ".r99", ".s00", etc, ".s99" or ".001", ".002", etc, for the "old" naming scheme.
    # base => Base output name, appended with ".sfv", ".partN" and/or ".rar" as appropriate. Default is the base name of the data file with extension removed.
    # naming-new => Force "new" Rar 3 volume naming scheme
    # naming-old => Force "old" volume naming scheme
    # Default: Automatically choose "old" or "new" Rar 3 volume naming scheme depending on the number of volumes needed. More than 101 invokes the "new" Rar 3 scheme.
    # unicode file names? (utf8, "unicode", none, auto none if not necessary)
    # Option to only do the first volume, the first few volumes, or any given set of volumes?
    
    help = False
    vol_max = 15 * 10 ** 6
    timestamp = None
    is_dryrun = False
    
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ("help", "-h", "--help", "-?", "?"):
            help = True
            i += 1
        elif "file" == arg:
            file = sys.argv[i + 1]
            i += 2
        elif "time" == arg:
            (timestamp, timestamp_frac) = get_timestamp(sys.argv[i + 1])
            i += 2
        elif "dryrun" == arg:
            is_dryrun = True
            i += 1
        else: raise Exception("""Bad command line argument: "{}".
Try "{} help".""".format(arg, sys.argv[0]))
    
    if help:
        print("""\
Options:
help\tDisplay this help
file <name-grp.avi>\tSpecify data file
time <yyyy>-<mm>-<dd>T<hh>:<mm>:<ss>[.<sssssss>]\tOverride data file
\ttimestamp\
dryrun\tDisplay file names and sizes but do not create them
""")
#~ time +/-s.sssssss\tAdjust data file timestamp
        return
    
    name_field = filename_encode(file)
    
    file_stat = os.stat(file)
    if timestamp is None:
        timestamp = time.localtime(file_stat.st_mtime)
        timestamp_frac = file_stat.st_mtime - int(file_stat.st_mtime)
    
    (dostime, xtime_field) = time_encode(timestamp, timestamp_frac)
    file_size = file_stat.st_size
    rr_max = calc_rr_count(vol_max)
    
    data_max = calc_prot_size(vol_max, rr_max)
    data_max -= len(RAR_ID) + MAIN_HDR_SIZE + \
        file_hdr_size(name_field, xtime_field)
    
    vol_count = quanta(file_size, data_max)
    base = os.path.splitext(os.path.basename(file))[0]
    vol_digits = 1 + int(math.log10(vol_count - 1))
    sfv_name = "{}.sfv".format(base)
    
    print("Number of volumes:", vol_count, file=sys.stderr)
    
    if not is_dryrun:
        volume = None
        sfv_file = None
        data = open(file, "rb")
        file_crc = 0
    try:
        sfv = set() if is_dryrun else dict()
        
        volnum = 0
        left = file_size
        while left > 0:
            is_first_vol = not volnum
            is_last_vol = left <= data_max
            data_size = left if is_last_vol else data_max
            
            new_numbering = False
            if new_numbering:
                volname = "{}.part{:0{digits}}.rar".format(base, 1 + volnum,
                    digits=vol_digits)
            elif is_first_vol:
                volname = "{}.rar".format(base)
            elif volnum - 1 < 100:
                volname = "{}.r{:02}".format(base, volnum - 1)
            else:
                volname = "{}.s{:02}".format(base, volnum - 1 - 100)
            
            print(volname, end=": ", file=sys.stderr)
            sys.stderr.flush()
            
            if not is_dryrun:
                volume = open(volname, "w+b")
            vol_size = 0
            
            if not is_dryrun: volume.write(RAR_ID)
            vol_size += len(RAR_ID)
            
            if not is_dryrun: write_main(volume, is_first_vol)
            vol_size += MAIN_HDR_SIZE
            
            if not is_dryrun: file_crc = write_file(volume, data,
                not is_first_vol, not is_last_vol, name_field, file_crc,
                dostime, xtime_field, file_size, data_size)
            vol_size += file_hdr_size(name_field, xtime_field) + data_size
            
            rr_count = rr_max
            if is_last_vol: rr_count = calc_rr_count(vol_size)
            if not is_dryrun: write_rr(volume, rr_count)
            vol_size += SUB_RR_HEADER_SIZE + \
                    quanta(vol_size, RR_SECT_SIZE) * RR_CRC_SIZE + \
                    rr_count * RR_SECT_SIZE
            
            if not is_dryrun:
                crc = write_end(volume, volnum, is_last_vol)
            vol_size += END_SIZE
            
            if not is_dryrun:
                volume.seek(-END_SIZE, io.SEEK_CUR)
                crc = crc32(volume.read(END_SIZE), crc)
                volume.close()
                volume = None
            
            if is_dryrun:
                sfv.add(volname)
                print("Size: {}".format(vol_size), file=sys.stderr)
            else:
                sfv[volname] = crc
                print("Size: {} CRC: {:08X}".format(vol_size, crc),
                    file=sys.stderr)
            
            left -= data_size
            volnum += 1
        
        if not is_dryrun:
            sfv_file = open(sfv_name, "wt", encoding="latin-1", newline="")
        sfv_size = 0
        
        for name in sorted(sfv):
            if not is_dryrun:
                sfv_file.write("{} {:08x}\r\n".format(name, sfv[name]))
            sfv_size += len(name) + 1 + 8 + 2
        
        if not is_dryrun:
            sfv_file.close()
            sfv_file = None
        print("{}: Size: {}".format(sfv_name, sfv_size), file=sys.stderr)
        
    finally:
        if not is_dryrun:
            if volume is not None: volume.close()
            if sfv_file is not None: sfv_file.close()
            data.close()

def get_timestamp(s):
    frac = re.search(r"(\.\d*)?$", s)
    tm = time.strptime(s[:frac.start()], "%Y-%m-%dT%H:%M:%S")
    frac = frac.group()
    if frac not in ("", "."):
        frac = float(frac)
    else:
        frac = 0
    return (tm, frac)

# Extra stuff appropriate for "rarfile" module

def write_main(volume, is_first_vol):
    write_block(volume,
        type=RAR_BLOCK_MAIN,
        flags=RAR_MAIN_VOLUME ^ RAR_MAIN_RECOVERY ^ \
            is_first_vol * RAR_MAIN_FIRSTVOLUME,
        data=(
            (0 for i in range(RAR_MAIN_EXTRA)),
        ))

RAR_MAIN_EXTRA = 2 + 4
MAIN_HDR_SIZE = S_BLK_HDR.size + RAR_MAIN_EXTRA

def write_file(volume, file, split_before, split_after, name, accum_crc,
dostime, xtime=None, size=None, pack_size=None):
    header_size = file_hdr_size(name, xtime)
    volume.seek(+header_size, io.SEEK_CUR)
    
    left = pack_size
    crc = 0 if split_after else accum_crc
    while left > 0:
        chunk = file.read(min(FILE_COPY_CRC_BUF, left))
        left -= len(chunk)
        volume.write(chunk)
        crc = crc32(chunk, crc)
        if split_after: accum_crc = crc32(chunk, accum_crc)
    
    flags = RAR_LONG_BLOCK ^ split_before * RAR_FILE_SPLIT_BEFORE ^ \
        split_after * RAR_FILE_SPLIT_AFTER ^ RAR_FILE_DICT4096 ^ \
        RAR_FILE_UNICODE
    parts = [
        S_FILE_HDR.pack(pack_size, size, RAR_OS_WIN32, crc, dostime, 20,
            ord("0"), len(name), 1 << 5),
        name,
    ]
    if xtime is not None:
        flags ^= RAR_FILE_EXTTIME
        parts.append(xtime)
    
    volume.seek(-pack_size - header_size, io.SEEK_CUR)
    write_block(volume, RAR_BLOCK_FILE, flags, parts)
    volume.seek(+pack_size, io.SEEK_CUR)
    
    if split_after: return accum_crc

def file_hdr_size(name, xtime):
    size = S_BLK_HDR.size + S_FILE_HDR.size + len(name)
    if xtime is not None: size += len(xtime)
    return size

def write_rr(volume, rr_count):
    prot_size = volume.tell()
    rr_crcs = bytearray()
    
    # Fast access to RR sectors as machine words instead of bytes.
    # The xor operation is endian-agnostic.
    # On an x86-64 computer,
    #     element-wise array.array("L") xor operation was about 8 times
    #         faster than for bytearray
    #     array-wise numpy.array(dtype=int) xor operation was about 3.5 times
    #         faster than for element-wise array.array("L")
    if USE_NUMPY: blank = lambda: numpy.frombuffer(bytearray(RR_SECT_SIZE),
        dtype=numpy.int)
    elif USE_ARRAY: blank = lambda: array.array("L", bytes(RR_SECT_SIZE))
    else: blank = lambda: bytearray(RR_SECT_SIZE)
    rr_sects = tuple(blank() for i in range(rr_count))
    
    volume.seek(0)
    left = prot_size
    slice = 0
    while left > 0:
        if left < RR_SECT_SIZE:
            chunk = volume.read(left). \
                ljust(RR_SECT_SIZE, bytes((0,)))
            left = 0
        else:
            chunk = volume.read(RR_SECT_SIZE)
            left -= RR_SECT_SIZE
        
        rr_crcs.extend(S_SHORT.pack(~crc32(chunk) & bitmask(16)))
        
        s = rr_sects[slice]
        if USE_NUMPY:
            s ^= numpy.frombuffer(chunk, dtype=numpy.int)
        elif USE_ARRAY:
            for (i, v) in enumerate(array.array("L", chunk)):
                s[i] ^= v
        else:
            for i in range(len(s)):
                s[i] ^= chunk[i]
        
        slice = (slice + 1) % rr_count
    
    # Why is this odd CRC initialiser used?
    crc = crc32(rr_crcs, 0xF0000000)
    for s in rr_sects: crc = crc32(s, crc)
    
    prot_sect_count = quanta(prot_size, RR_SECT_SIZE)
    size = prot_sect_count * RR_CRC_SIZE + rr_count * RR_SECT_SIZE
    
    write_block(volume,
        type=RAR_BLOCK_SUB,
        flags=RAR_LONG_BLOCK ^ RAR_SKIP_IF_UNKNOWN,
        data=(
            S_FILE_HDR.pack(size, size, RAR_OS_WIN32, crc, 0, 29, ord("0"),
                len(SUB_RR_NAME), 0),
            SUB_RR_NAME,
            SUB_RR_PROTECT,
            S_LONG.pack(rr_count),
            struct.pack("<Q", prot_sect_count),
        ))
    volume.write(rr_crcs)
    for s in rr_sects: volume.write(s)

def calc_rr_count(total):
    if total < RR_SECT_SIZE:
        return 1
    
    # Default recovery data size is 0.6%
    rr = total * 6 // RR_SECT_SIZE // 1000 + 2
    
    if rr >= RR_MAX: return RR_MAX
    
    if rr < 6: return rr
    else: return rr | 1 # Always an odd number

# Calculate space available for RR-protected data
def calc_prot_size(volsize, rr_count):
    # Allocate space for all RR-protected data and sector CRCs
    space = volsize - SUB_RR_HEADER_SIZE - rr_count * RR_SECT_SIZE - END_SIZE
    
    # Last quantum is useless if it cannot fit a CRC and any data
    last_q = last_quantum(space, RR_QUANTUM)
    if last_q <= RR_CRC_SIZE: space -= last_q
    
    prot_sect_count = quanta(space, RR_QUANTUM)
    return space - prot_sect_count * RR_CRC_SIZE

RR_MAX = 524288
RR_SECT_SIZE = 512
RR_CRC_SIZE = 2
SUB_RR_NAME = b"RR"
SUB_RR_PROTECT = b"Protect+"
SUB_RR_HEADER_SIZE = S_BLK_HDR.size + S_FILE_HDR.size + len(SUB_RR_NAME) + \
    len(SUB_RR_PROTECT) + 4 + 8

# One quantum of space for each CRC
RR_QUANTUM = RR_SECT_SIZE + RR_CRC_SIZE

def write_end(volume, volnum, is_last_vol):
    left = volume.tell()
    volume.seek(0)
    crc = 0
    while left > 0:
        chunk = volume.read(min(FILE_CRC_BUF, left))
        left -= len(chunk)
        crc = crc32(chunk, crc)
    
    write_block(volume,
        type=RAR_BLOCK_ENDARC,
        flags=RAR_SKIP_IF_UNKNOWN ^
            (not is_last_vol) * RAR_ENDARC_NEXT_VOLUME ^ RAR_ENDARC_DATACRC ^
            RAR_ENDARC_REVSPACE ^ RAR_ENDARC_VOLNR,
        data=(
            S_LONG.pack(crc),
            S_SHORT.pack(volnum),
            (0 for i in range(END_EXTRA)),
        ))
    
    return crc

END_EXTRA = 7
END_SIZE = S_BLK_HDR.size + 4 + 2 + END_EXTRA

def filename_encode(name):
    field = bytearray(name, "latin-1")
    field.append(0)
    field.append(1) # Default MSB observed in the wild
    pos = 0
    left = len(name)
    while left > 0:
        opcode_byte = 0
        opcode_pos = BYTE_BITS
        chunk = bytearray()
        
        while opcode_pos >= FILENAME_OPCODE_BITS and left > 0:
            opcode_pos -= FILENAME_OPCODE_BITS
            
            if 1 == left:
                opcode = FILENAME_8_BIT
                chunk.append(ord(name[pos]))
                left = 0
            else:
                opcode = FILENAME_COPY
                size = min(COPY_LEN_MIN + bitmask(COPY_LEN_BITS), left)
                chunk.append(0 << COPY_MSB_BIT | size - COPY_LEN_MIN)
                pos += size
                left -= size
            
            opcode_byte |= opcode << opcode_pos
        
        field.append(opcode_byte)
        field.extend(chunk)
    
    return field

FILENAME_8_BIT = 0
FILENAME_MSB = 1
FILENAME_16_BIT = 2
FILENAME_COPY = 3

FILENAME_OPCODE_BITS = 2

COPY_LEN_MIN = 2
COPY_LEN_BITS = 7
COPY_MSB_BIT = 7

def time_encode(tm, frac=0):
    dostime = \
        tm.tm_sec >> 1 << 0 ^ \
        tm.tm_min << 5 ^ \
        tm.tm_hour << 11 ^ \
        tm.tm_mday << 16 ^ \
        tm.tm_mon << 21 ^ \
        tm.tm_year - 1980 << 25
    
    one_sec = tm.tm_sec & 1
    if not frac and not one_sec: return (dostime, None)
    
    flags = 1 << TIME_VALID_BIT
    flags |= one_sec << TIME_ONE_BIT
    frac = int(frac * 10 ** 7)
    
    precision = TIME_FRAC_BYTES
    while precision > 0:
        if frac & 0xFF: break
        frac >>= 8
        --precision
    flags |= precision
    
    xtime = bytearray(S_SHORT.pack(
        flags << MTIME_INDEX * TIME_FLAG_BITS))
    
    for i in range(precision):
        xtime.append(frac & 0xFF)
        frac >>= 8
    
    return (dostime, xtime)

MTIME_INDEX = 3
TIME_FLAG_BITS = 4
TIME_VALID_BIT = 3
TIME_ONE_BIT = 2
TIME_FRAC_BYTES = 3

# The "rar_decompress" function creates a Rar file but not in a reusable way
def write_block(file, type, flags, data):
    block = bytearray()
    for part in data: block.extend(part)
    
    header = S_BLK_HDR_DATA.pack(type, flags, S_BLK_HDR.size + len(block))
    
    crc = crc32(header)
    crc = crc32(block, crc)
    file.write(S_SHORT.pack(crc & bitmask(16)))
    
    file.write(header)
    file.write(block)

S_BLK_HDR_DATA = Struct("<BHH") # S_BLK_HDR without the CRC field prepended

def bitmask(size): return ~(~0 << size)
BYTE_BITS = 8
def quanta(total, quantum): return (total - 1) // quantum + 1
def last_quantum(total, quantum): return (total - 1) % quantum + 1

if "__main__" == __name__: main()
