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

sys.path.append('../../libcif/lib')
from CIF.CtrlCommands.Clients import *
from CIF.CtrlCommands.Ping import *

from CIF.Foundation import Foundation
from DB.APIKeys import *
from DB.Exploder import Exploder
from DB.Registry import Registry

print "cif-db proof of concept"

"""
Two threads:

Attach to cif-router PUB:
    Subscribe to all message types
    Write all messages we receive to HBase

Attach to cif-router ROUTER:
    When we receive a query request:
        retrieve the requested information
        send it back to the requester
"""




    
def usage():
    print "\
    # poc-db [-c 5656] [-r cif-router:5555] [-m name]\n\
    #     -c  control port (REQ - for inbound messages)\n\
    #     -r  cif-router hostname:port\n\
    #     -m  my name\n"
    

def HBConnection(host):
    c = happybase.Connection(host)
    t = c.tables()
    print "found tables: ", t
    if not "cif_idl" in t:
        raise Exception("missing cif_idl table")
    if not "cif_objs" in t:
        raise Exception("missing cif_objs table")
    return c

"""
Given a msg object, we want to record its IDL (for posterity)
to cif_idl if it hasn't been already. We then write the actual object
to cif_objs.

#     rowkey $salt$timestamp$hash   (eg "<2 byte salt><8 byte timestamp><16 byte md5>")
#     cf:$submsgtype     (eg cf:RFC5070-IODEF-v1=object)



"""

def saveIDL(cif_idl, sr):
    #bot = sr.baseObjectType;
    bot = re.sub('_', '-', sr.baseObjectType)
    fn = cifsupport.installBase() + "/" + bot + ".proto"
    print "IDL should be: " + fn

def writeToDb(cif_objs, cif_idl, sr):
    print "\tWrite message(s) to db: "  + str(sr.baseObjectType)
    ts = int(time.time()) # ignore fractional seconds
    md5 = hashlib.md5()
    md5.update(sr.SerializeToString())
    hash = md5.digest()
    colspec = "cf:" + str(sr.baseObjectType)
    salt = 0xFF00  # FIX
    try:
        saveIDL(cif_idl, sr)
        rowid = struct.pack(">HI16s", salt, ts, hash)
        cif_objs.put(rowid, {colspec: sr.data})
        print "\tput: rowid:" + rowid.encode('hex') + " " + colspec + " "
    except struct.error, err:
        print "Failed to pack rowid: ", err

def apikey_row_to_akr(row):
        akr = control_pb2.APIKeyResponse()
        akr.alias = row['alias']
        akr.revoked = row['revoked']
        akr.expires = row['expires']
        akr.restrictedAccess = row['restrictedAccess']
        akr.writeAccess = row['writeAccess']
        akr.description = row['description']
        akr.created = row['created']
        akr.parent = row['parent']
        
        akgl = []
        for group in row['groups']:
            akg = control_pb2.APIKeyGroup()
            akg.groupname = row['groups'][group]
            akg.groupid = group
            if akg.groupid == row['defaultGroup']:
                akg.default = True
            else:
                akg.default = False
            akgl.append(akg)
        
        akr.groupsList.extend(akgl)
        
        return akr
                
def controlMessageHandler(msg):
    if debug > 0:
        print "controlMessageHandler: Got a control message: "#, msg
        
    if msg.type == control_pb2.ControlType.COMMAND:
        if msg.command == control_pb2.ControlType.PING:
            c = Ping.makereply(msg)
            cf.sendmsg(c, None)
            
        elif msg.command == control_pb2.ControlType.APIKEY_GET:
            print "controlMessageHandler: APIKEY_GET ", msg.apiKeyRequest.apikey
            k = apikeys.get_by_key(msg.apiKeyRequest.apikey)
            msg.type = control_pb2.ControlType.REPLY
            if k == {}:
                print "APIKEY_GET Key lookup failed."
                msg.status = control_pb2.ControlType.FAILED
            else:
                print "APIKEY_GET Key lookup succeeded."
                msg.status = control_pb2.ControlType.SUCCESS
                akr = apikey_row_to_akr(k)
                akr.apikey = msg.apiKeyRequest.apikey

                msg.apiKeyResponseList.extend([akr])
            tmp = msg.dst
            msg.dst = msg.src
            msg.src = tmp
            print "controlMessageHandler: APIKEY_GET sending reply.."
            cf.sendmsg(msg, None)
            
        elif msg.command == control_pb2.ControlType.APIKEY_LIST:
            print "controlMessageHandler: APIKEY_LIST ", msg.apiKeyRequest.apikey
            ks = apikeys.list_by_key(msg.apiKeyRequest.apikey)
            akr_list = []
            
            for kkey in ks:
                kval = ks[kkey]
                akr = apikey_row_to_akr(kval)
                akr.apikey = kkey
                akr_list.append(akr)
            msg.apiKeyResponseList.extend(akr_list)
            tmp = msg.dst
            msg.dst = msg.src
            msg.src = tmp
            msg.type = control_pb2.ControlType.REPLY
            msg.status = control_pb2.ControlType.SUCCESS
            print msg
            cf.sendmsg(msg, None)
            
        elif msg.command == control_pb2.ControlType.APIKEY_ADD:
            print "controlMessageHandler: APIKEY_ADD ", msg.apiKeyRequest.apikey
            msg.type = control_pb2.ControlType.REPLY
            tmp = msg.dst
            msg.dst = msg.src
            msg.src = tmp

            try:
                apikeys.add_key(msg.apiKeyRequest)
                msg.status = control_pb2.ControlType.SUCCESS
            except Exception as e:
                print "FAILED with " + str(e)
                msg.statusMsg = str(e)
                msg.status = control_pb2.ControlType.FAILED
                
            cf.sendmsg(msg, None)
            
        elif msg.command == control_pb2.ControlType.APIKEY_UPDATE:
            print "controlMessageHandler: APIKEY_UPDATE ", msg.apiKeyRequest.apikey
            msg.type = control_pb2.ControlType.REPLY
            tmp = msg.dst
            msg.dst = msg.src
            msg.src = tmp
            try:
                apikeys.update_key(msg.apiKeyRequest)
                msg.status = control_pb2.ControlType.SUCCESS
            except Exception as e:
                msg.status = control_pb2.ControlType.FAILED
                print "FAILED with " + str(e)
                msg.statusMsg = str(e)
            cf.sendmsg(msg, None)
            
        elif msg.command == control_pb2.ControlType.APIKEY_DEL:
            print "controlMessageHandler: APIKEY_DEL ", msg.apiKeyRequest.apikey
            tmp = msg.dst
            msg.dst = msg.src
            msg.src = tmp

            msg.status = control_pb2.ControlType.FAILED

            try:
                if msg.apiKeyRequest.apikey == '' and msg.apiKeyRequest.alias != '':
                    apikeys.remove_by_alias(msg.apiKeyRequest.alias)
                else:
                    apikeys.remove_by_key(msg.apiKeyRequest.apikey)
                msg.status = control_pb2.ControlType.SUCCESS
            except Exception as e:
                msg.statusMsg = str(e)
                
            cf.sendmsg(msg, None)
            
            
try:
    opts, args = getopt.getopt(sys.argv[1:], 'c:r:m:D:h')
except getopt.GetoptError, err:
    print str(err)
    usage()
    sys.exit(2)

controlport = "5656"
cifrouter = "sdev.nickelsoft.com:5555"
myid = "cif-db"
apikey = "a8fd97c3-9f8b-477b-b45b-ba06719a0088"
debug = 0

for o, a in opts:
    if o == "-c":
        controlport = a
    elif o == "-m":
        myid = a
    elif o == "-r":
        cifrouter = a
    elif o == "-h":
        usage()
        sys.exit(2)
    elif o == "-D":
        debug = a

myip = socket.gethostbyname(socket.gethostname()) # has caveats

global cf
global exploder

try:
    hbhost = "localhost"
    
    print "Connect to HBase"
    connection = HBConnection(hbhost)
    cif_objs = connection.table('cif_objs').batch(batch_size=5) # set very low for development, set to 1000+ for test/qa/prod
    cif_idl = connection.table('cif_idl')
    
    print "Init Registry"
    registry = Registry(hbhost, debug)
    num_servers = registry.get('hadoop.num_servers')
    if num_servers == None:
        num_servers = 1
        print "hadoop.num_servers not set. defaulting."
    print "hadoop.num_servers = ", num_servers
    
    global apikeys
    
    print "Initializing APIKeys object"
    apikeys = APIKeys(connection, True)
    
    print "Resolving our APIKey: " + myid
    
    apikey = apikeys.get_by_alias(myid)
    
    print "Initializing foundation"
    
    cf = Foundation({'apikey' : apikey,
                     'myip'   : myip,
                     'cifrouter' : cifrouter,
                     'controlport' : controlport,
                     'myid' : myid,
                     'routerid' : "cif-router"
                     })

    print "Configuring foundation"
    
    cf.setdebug(debug)
    cf.setdefaultcallback(controlMessageHandler)
    
    print "Register with " + cifrouter + " (req->rep)"
    req = cf.ctrlsocket()

    # apikey, req, myip, myid, cifrouter
    (routerport, routerpubport) = cf.register()

    subscriber = cf.subscribersocket()
    
    time.sleep(1) # wait for router to connect, sort of lame but see this a lot in zmq code
    
        
    print "Initializing Exploder"
    exploder = Exploder.Exploder(hbhost, False)
    exploder.do_some_work()
    
    while True:
        msg = msg_pb2.MessageType()
        msg.ParseFromString(subscriber.recv())
        exploder.do_some_work()

        
        if apikeys.is_valid(msg.apikey):
            if msg.type == msg_pb2.MessageType.SUBMISSION and len(msg.submissionRequest) > 0:
                print "Got a SUBMISSION. Saving."
                for i in range(0, len(msg.submissionRequest)):
                    writeToDb(cif_objs, cif_idl, msg.submissionRequest[i])
            
            elif msg.type == msg_pb2.MessageType.QUERY and len(msg.queryRequest) > 0:
                print "Got a QUERY. Processing."
                for i in range(0, len(msg.submissionRequest)):
                    qreply = readFromDb(msg.queryRequest[0])
                    msg.reply.append(qreply)
                print "Gathered replies from DB. Sending back to requester. TODO"
            
            else:
                print "Wrong or empty message recvd on subscriber port. Expected submission or query (" + \
                    str(msg_pb2.MessageType.SUBMISSION) + " or " +                               \
                    str(msg_pb2.MessageType.QUERY) + ")  got " +                                 \
                    str(msg.type) + " number of parts (should be > 0) SR:" +                     \
                    str(len(msg.submissionRequest)) + " / QR:" + str(len(msg.queryRequest)) 
        else:
            print "message has an invalid apikey"
            
    cf.unregister()
    
except KeyboardInterrupt:
    if cif_objs != None:
        cif_objs.send() # flush
    if cf != None:
        cf.ctrlc()
except IOError as e:
    print "I/O error({0}): {1}".format(e.errno, e.strerror)
except KeyError as e:
    print "PB KeyError: ", e
    traceback.print_exc(file=sys.stdout)
except Exception as inst:
    print "Unexpected error: ", sys.exc_info()[0], " ", sys.exc_info()[1], " "
    traceback.print_tb(sys.exc_info()[2])
except TTransportException as e:
    print "Can't connect to HBase"
    
