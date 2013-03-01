import syslog
from datetime import datetime
import time
import re
import sys
import threading
import happybase
import struct

sys.path.append('/usr/local/lib/cif-protocol/pb-python/gen-py')

import msg_pb2
import feed_pb2
import control_pb2
import RFC5070_IODEF_v1_pb2
import MAEC_v2_pb2
import cifsupport

import Botnet
from DB.Salt import Salt

class Exploder(object):
    def __init__ (self, connection, debug):
        self.debug = debug
        self.dbh = connection
        t = self.dbh.tables()

        self.table = self.dbh.table('infrastructure_botnet')
        self.kickit = threading.Semaphore(0)
        self.proc_thread = threading.Thread(target=self.run, args=())
        self.proc_thread.start()
        
        self.botnet_handler = Botnet.Botnet(connection, debug)
        
        self.salt = Salt()
        
        
    def L(self, msg):
        caller =  ".".join([str(__name__), sys._getframe(1).f_code.co_name])
        if self.debug != None:
            print caller + ": " + msg
        else:
            syslog.syslog(caller + ": " + msg)
    
    def do_some_work(self):
        self.kickit.release()
        
    def getcheckpoint(self):
        t = self.dbh.table('registry')
        c = t.row('exploder_checkpoint')
        if c != None and 'b:ts' in c:
            return c['b:ts']
        return 0
    
    def setcheckpoint(self, ts):
        t = self.dbh.table('registry')
        t.put('exploder_checkpoint', { 'b:ts': ts })
    
    def run(self):
        self.L("Exploder running")
        
        while True:
            self.L("waiting for work")
            self.kickit.acquire() # will block provided kickit is 0
            self.L("wakup")
            
            co = self.dbh.table('cif_objs')
            
            self.L("connected to cif_objs")
            
            startts = self.getcheckpoint()
            endts = int(time.time())
            processed = 0

            self.L("processing: " + str(startts) + " to " + str(endts))
            
            salt = 0xFF00
            srowid = struct.pack(">HIIIII", salt, startts, 0,0,0,0)
            erowid = struct.pack(">HIIIII", salt, endts, 0,0,0,0)

            for key, data in co.scan(row_start=srowid, row_stop=erowid):
                contains = data.keys()[0]
                obj_data = data[contains]
                
                if contains == "cf:RFC5070_IODEF_v1_pb2":
                    iodef = RFC5070_IODEF_v1_pb2.IODEF_DocumentType()
                    try:
                        iodef.ParseFromString(obj_data)

                        #print iodef
                        ii = iodef.Incident[0]
                        table_type = ii.Assessment[0].Impact[0].content.content
                        rowkey = None
                        
                        if table_type == "botnet":
                            self.L("botnet")
                            self.botnet_handler.extract(iodef)
                            self.botnet_handler.commit()
                            
                        elif table_type == "malware":
                            self.L("malware")
                                
                    except Exception as e:
                        print "Failed to parse restored object: ", e

    
            #self.setcheckpoint(endts+1)
            