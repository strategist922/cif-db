#!/usr/bin/python


import sys
import zmq
import random
import time
import os
import datetime
import json
import getopt
import socket
import happybase
import hashlib
import struct
import traceback
import re

# adjust to match your $PREFIX if you specified one
# default PREFIX = /usr/local
sys.path.append('/usr/local/lib/cif-protocol/pb-python/gen-py')

import msg_pb2
import feed_pb2
import control_pb2
import RFC5070_IODEF_v1_pb2
import MAEC_v2_pb2
import cifsupport

def HBConnection(host):
    c = happybase.Connection(host)
    t = c.tables()
    if not "cif_idl" in t:
        raise Exception("missing cif_idl table")
    if not "cif_objs" in t:
        raise Exception("missing cif_objs table")
    return c

def tots(dt):
    if dt != None:
        return int(time.mktime(time.strptime(dt, "%Y-%m-%d-%H-%M-%S")))
    return 0
    
def usage():
    print "\
    db-scan.py [-t tablename] [-s starttime] [-e endtime]\n\
        -t  cif_objs\n\
           -s  start time YYYY-MM-DD-HH-MM-SS (def: start-5mins)\n\
           -e  start time YYYY-MM-DD-HH-MM-SS (def: now)\n\
        -t  infrastructure_botnet\n\
    hour is in 24 hour format\n"
    

def dump_cif_objs(starttime, endtime):
    salt = 0xFF00
    srowid = struct.pack(">HIIIII", salt, starttime, 0,0,0,0)
    erowid = struct.pack(">HIIIII", salt, endtime, 0,0,0,0)
    
    print "start ", hex(salt), srowid.encode('hex') 
    print "end   ", erowid.encode('hex')
    
    connection = HBConnection('localhost')
    tbl = connection.table('cif_objs')
    
    print "Dumping cif_objs"
    
    count = 0
    
    for key, data in tbl.scan(row_start=srowid, row_stop=erowid):
        psalt, pts = struct.unpack(">HI", key[:6])
        print "entered on: ", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pts))
        contains = data.keys()[0]
        obj_data = data[contains]
        print "contains: ", contains
        count = count + 1
    
        if contains == "cf:RFC5070_IODEF_v1_pb2":
            iodef = RFC5070_IODEF_v1_pb2.IODEF_DocumentType()
    
            try:
                iodef.ParseFromString(obj_data)
                
                ii = iodef.Incident[0]
                table_type = ii.Assessment[0].Impact[0].content.content
                confidence = ii.Assessment[0].Confidence.content
                severity = ii.Assessment[0].Impact[0].severity
                addr_type = ii.EventData[0].Flow[0].System[0].Node.Address[0].category
                
                addr = ii.EventData[0].Flow[0].System[0].Node.Address[0].content
            
                prefix = 'na'
                asn = 'na'
                asn_desc = 'na'
                rir = 'na'
                cc = 'na'
                
                # addr_type == 5 then AddtData will contain asn, asn_desc, cc, rir, prefix
                if addr_type == RFC5070_IODEF_v1_pb2.AddressType.Address_category_ipv4_addr:
                    for i in ii.EventData[0].Flow[0].System[0].AdditionalData:
                        if i.meaning == 'prefix':
                            prefix = i.content
                        elif i.meaning == 'asn':
                            asn = i.content
                        elif i.meaning == 'asn_desc':
                            asn_desc = i.content
                        elif i.meaning == 'rir':
                            rir = i.content
                        elif i.meaning == 'cc':
                            cc = i.content
    
                                
                print "\ttype: ", table_type
                print "\tconfidence: ", confidence
                print "\tseverity: ", severity
                print "\taddr_type: ", addr_type
                print "\taddr: ", addr, prefix, asn, asn_desc, rir, cc
                
            except Exception as e:
                print "Failed to restore message to stated type: ", e
    
        
    print count, " rows total."

def dump_infrastructure_botnet():
    """
    To search for ipv4:
       for salt in server_list:
          startrowkey = salt + 0x0 + ipv4/packed
          endrowkey = salt + 0x0 + ipv4+1/packed
          scan()...
    
    ipv4/packed can be a full address, or just a prefix, and the fact that
    the rowkeys are stored sorted will take care of locating the right records
    
    if ipv4 is a prefix eg 0x80CD0000 then ipv4+1/packed will be 0x80CE0000 
    
    if ipv4 is not a prefix, then the row is directly fetched instead of 
    performing a scan
    """
    print "Dumping infrastructure_botnet"
    connection = HBConnection('localhost')
    tbl = connection.table('infrastructure_botnet')
    
    count = 0
    
    # key is 2 byte salt + 16 byte address field 
    for key, data in tbl.scan():
        psalt, atype = struct.unpack(">HB", key[:3])
        if atype == 0x0:
            o1, o2, o3, o4 = struct.unpack(">BBBB", key[3:7])
            print ".".join([str(o1), str(o2), str(o3), str(o4)]),
            for cn in ['prefix', 'asn', 'asn_desc','rir', 'cc', 'confidence', 'port', 'proto']:
                print data['b:' + cn], "|\t", 
            print "\n"
        elif type == 0x1:
            print "ipv6"
        elif type == 0x2:
            print "fqdn"

                
try:
    opts, args = getopt.getopt(sys.argv[1:], 't:s:e:D:h')
except getopt.GetoptError, err:
    print str(err)
    usage()
    sys.exit(2)
    
debug = 0
starttime = -1
endtime = -1
table_name = None

for o, a in opts:
    if o == "-t":
        table_name = a
    elif o == "-s":
        starttime = tots(a)
    elif o == "-e":
        endtime = tots(a)
    elif o == "-h":
        usage()
        sys.exit(2)
    elif o == "-D":
        debug = a

if table_name == "cif_objs":
    if starttime == -1 and endtime != -1:
        starttime = endtime - 300
    elif starttime != -1 and endtime == -1:
        endtime = starttime + 300
    elif starttime == -1 and endtime == -1:
        endtime = time.time()
        starttime = endtime - 300

    print "cif_objs: start=", starttime, " end=", endtime

    dump_cif_objs(starttime, endtime)
    
if table_name == "infrastructure_botnet":
    dump_infrastructure_botnet()
    





