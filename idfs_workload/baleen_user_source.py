"""Portable BFR1 layout used by the frozen Region2 frequency tables.

All fields are big-endian. The header contains magic, version, container size,
object size, second, request count, byte count, entry count, reads and writes.
Each record contains a 64-bit container offset, transfer size, operation
(1=read, 2=write) and frequency. key-map.csv binds offsets to source identities.
"""
import struct

HEADER = struct.Struct('>iiqiiqqiqq')
RECORD = struct.Struct('>qiIq')
