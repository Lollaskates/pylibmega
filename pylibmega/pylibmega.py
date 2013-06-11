###
#   Small portions of this file are referenced from Richard O'Dwyer's mega.py - https://github.com/richardasaurus/mega.py
#   Specifically, the general Mega class (framework, function names).
#
#   @author: Geoffrey Tucker - https://github.com/Lollaskates/
#   @date: 2013-06-11 (initial commit)
###

import re
import json
from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Util import Counter
import os
import random
import binascii
import requests
import time
import shutil
import sys
from .errors import ValidationError, RequestError
from .crypto import *
import threading, Queue
import tempfile
import traceback
import weakref

class Mega_Processor(threading.Thread):
    """Individual processor, works off of queue."""
    def __init__(self,filedownloadqueue=None,fileuploadqueue=None,messagequeue=None,errorqueue=None,megaobj=None,args=None):
        threading.Thread.__init__(self)
        self.processortype = None
        self.inqueue = None
        self.outqueue = messagequeue
        self.errqueue = errorqueue
        self.args = args
        if self.args == None:
            self.args = {}
            self.args['maxworkers'] = 6
        self.args['chunk-multiplier'] = 3
        if filedownloadqueue:
            self.processortype = 'download-processor'
            self.inqueue = filedownloadqueue
            self.downloadqueue = Queue.Queue()
            self.decryptqueue = Queue.Queue()
            self.writequeue = Queue.Queue()
        elif fileuploadqueue:
            self.processortype = 'upload-processor'
            self.inqueue = fileuploadqueue
            self.uploadqueue = Queue.Queue()
            self.encryptqueue = Queue.Queue(100) #tentatively max of 100 entries (100*3) = 300MB
            self.readqueue = Queue.Queue()
        else:
            print 'No processor type specified. Bailing out. (__Mega_Processor.__init__)'
            self.join()
        self.parentmegaobj = megaobj
        self.workers = self.__initialize_workers(self.args['maxworkers'])
            
    def __initialize_workers(self,maxworkers):
        workers = {}
        if self.processortype == 'download-processor':
            dlers = []
            for i in range(maxworkers):
                d = Mega_Worker(downloadqueue=self.downloadqueue,decryptqueue=self.decryptqueue,messagequeue=self.outqueue,errorqueue=self.errqueue,megaobj=self.parentmegaobj)
                d.setDaemon(True)
                d.start()
                dlers.append(d)
                
            deers = []
            d = Mega_Worker(decryptqueue=self.decryptqueue,writequeue=self.writequeue,messagequeue=self.outqueue,errorqueue=self.errqueue,megaobj=self.parentmegaobj)
            d.setDaemon(True)
            d.start()
            deers.append(d)

            wrers = []
            d = Mega_Worker(writequeue=self.writequeue,messagequeue=self.outqueue,errorqueue=self.errqueue,megaobj=self.parentmegaobj)
            d.setDaemon(True)
            d.start()
            wrers.append(d)
                
            workers = {'downloader': dlers,
                       'decrypter': deers,
                       'writer': wrers,
                      }
        elif self.processortype == 'upload-processor':
            ulers = []
            for i in range(maxworkers):
                u = Mega_Worker(uploadqueue=self.uploadqueue,messagequeue=self.outqueue,altqueue=self.encryptqueue,errorqueue=self.errqueue,megaobj=self.parentmegaobj)
                u.setDaemon(True)
                u.start()
                ulers.append(u)

            eners = []
            u = Mega_Worker(encryptqueue=self.encryptqueue,uploadqueue=self.uploadqueue,messagequeue=self.outqueue,errorqueue=self.errqueue,megaobj=self.parentmegaobj)
            u.setDaemon(True)
            u.start()
            eners.append(u)
            
            reers = []
            u = Mega_Worker(encryptqueue=self.encryptqueue,readqueue=self.readqueue,messagequeue=self.outqueue,errorqueue=self.errqueue,megaobj=self.parentmegaobj)
            u.setDaemon(True)
            u.start()
            reers.append(u)
            
            workers = {'uploader': ulers,
                       'encrypter': eners,
                       'reader': reers,
                      }
        else:
            print 'No processor type specified. Bailing out. (___Mega_Processor._initialize_workers)'
            self.join()
        return workers
    
    def run(self):
        if self.processortype == 'download-processor':
            while True:
                try:
                    self.currentfile = self.inqueue.get()
                    
                    #prepare decrypter (configure args)
                    self.decryptqueue.put({'k': self.currentfile['k'],
                                           'iv': self.currentfile['iv'],
                                           'meta_mac': self.currentfile['meta_mac'],
                                           'file_size': self.currentfile['file_size'],
                                        })
                    
                    #prepare writer (open for writing)
                    self.writequeue.put({'file_size': self.currentfile['file_size'],
                                         'file_name': self.currentfile['file_name'],
                                         'dest_path': self.currentfile['dest_path'],
                                         'temp_output_file': self.currentfile['temp_output_file']})
                    
                    #add work to download queue (try to optimize chunk downloads based on filesize and latency to mega)
                    chunkList = [(chunk_start,chunk_size) for chunk_start, chunk_size in get_chunks(self.currentfile['file_size'])]
                        #if chunk_size == 1048576: #max individual chunk size
                    for i in xrange(0,len(chunkList),self.args['chunk-multiplier']):
                        temp = chunkList[i:i+self.args['chunk-multiplier']]
                        csize = sum(size[1] for size in temp)
                        cstart = temp[0][0] #get starting offset from first chunk
                        #self.outqueue.put({'thread-type': self.processortype,'thread-args': self.args, 'thread-message': 'chunk_start: '+str(cstart)+' / chunk_size: '+str(csize)})
                        self.downloadqueue.put({'chunks': temp,
                                                'file_name': self.currentfile['file_name'],
                                                'download_url': self.currentfile['file_url']+'/'+str(cstart)+'-'+str(cstart+(csize-1))})
                    
                    #wait until file is completely processed
                    self.downloadqueue.join()
                    self.decryptqueue.join()
                    self.writequeue.join()
                    
                    self.outqueue.put({'thread-type': self.processortype,'thread-args': self.args, 'thread-message': 'download-processor finished.'})
                    self.inqueue.task_done()
                except:
                    self.errqueue.put({'thread-type': self.processortype,'thread-args': self.args,'thread-error': traceback.format_exc()})
        elif self.processortype == 'upload-processor':
            while True:
                try:
                    self.currentfile = self.inqueue.get()
                    
                    #prepare encrypter (configure args)
                    self.encryptqueue.put({ 'dest' : self.currentfile['dest'],
                                            'dest_filename': self.currentfile['dest_filename'],
                                            'file_name': self.currentfile['file_name'],
                                            'file_size': self.currentfile['file_size'],
                                            'ul_url': self.currentfile['ul_url'],
                                            'ul_key': self.currentfile['ul_key'],
                                            'k_str': self.currentfile['k_str'],
                                            'count': self.currentfile['count'],
                                            'aes': self.currentfile['aes'],
                                            'completion_file_handle': self.currentfile['completion_file_handle'],
                                            'mac_str': self.currentfile['mac_str'],
                                            'mac_encryptor': self.currentfile['mac_encryptor'],
                                            'iv_str': self.currentfile['iv_str'],
                                            'attribs': self.currentfile['attribs'],
                                            'master_key': self.currentfile['master_key']
                                        })
                    
                    #prepare reader (open for reading)
                    self.readqueue.put({'file_size': self.currentfile['file_size'],
                                         'file_name': self.currentfile['file_name'],
                                         'input_file': self.currentfile['input_file']})
                    
                    #add work to upload queue (try to optimize chunk uploads based on filesize and latency to mega)
                    chunkList = [(chunk_start,chunk_size) for chunk_start, chunk_size in get_chunks(self.currentfile['file_size'])]
                    for i in xrange(0,len(chunkList),self.args['chunk-multiplier']):
                        temp = chunkList[i:i+self.args['chunk-multiplier']]
                        chunks = []
                        for chunk in temp:
                            chunks.append({'chunk_stats': chunk})
                        #self.outqueue.put({'thread-type': self.processortype,'thread-args': self.args, 'thread-message': 'chunk_start: '+str(cstart)+' / chunk_size: '+str(csize)})
                        self.readqueue.put({'chunks': chunks})
                    
                    #wait until file is completely processed
                    self.readqueue.join()
                    self.encryptqueue.join()
                    self.uploadqueue.join()
                    self.encryptqueue.join() #needs a second one, because we have things to do after the uploads are done.
                    self.outqueue.put({'thread-type': self.processortype,'thread-args': self.args, 'thread-message': 'upload-processor finished.'})
                    self.inqueue.task_done()
                except:
                    self.errqueue.put({'thread-type': self.processortype,'thread-args': self.args,'thread-error': traceback.format_exc()})
        else:
            print "self.processortype still not set. Shouldn't have gotten here."
        self.join()
    
class Mega_Worker(threading.Thread):
    """Individual worker, works off of queue."""
    def __init__(self,downloadqueue=None,uploadqueue=None,encryptqueue=None,decryptqueue=None,writequeue=None,readqueue=None,altqueue=None,messagequeue=None,errorqueue=None,megaobj=None,args=None):
        threading.Thread.__init__(self)
        self.workertype = None
        self.inqueue = None
        self.outqueue = None
        self.msgqueue = messagequeue
        self.errqueue = errorqueue
        self.altqueue = altqueue
        self.args = args
        self.parentmegaobject = megaobj
        if self.args == None:
            self.args = {}
        self.args['bytesprocessed'] = 0
        self.args['partscompleted'] = 0
        self.args['currentfile'] = ''
        self.args['timeout'] = 160
        self.cache = []
        self.altcache = []

        if downloadqueue and decryptqueue:
            self.workertype = 'downloader'            
            self.inqueue = downloadqueue
            self.outqueue = decryptqueue
        elif decryptqueue and writequeue:
            self.workertype = 'decrypter'
            self.inqueue = decryptqueue
            self.outqueue = writequeue
        elif encryptqueue and uploadqueue:
            self.workertype = 'encrypter'
            self.inqueue = encryptqueue 
            self.outqueue = uploadqueue 
        elif readqueue and encryptqueue:
            self.workertype = 'reader'
            self.inqueue = readqueue 
            self.outqueue = encryptqueue
        elif uploadqueue:
            self.workertype = 'uploader'
            self.inqueue = uploadqueue
            self.altqueue = altqueue
        elif writequeue:
            self.workertype = 'writer'
            self.inqueue = writequeue 
        else:
            print 'No workertype specified. Bailing out.'
            self.join()
        
    def run(self):
        if self.workertype == 'downloader':
            while True:
                download_request = self.inqueue.get()
                if self.args['currentfile'] != download_request['file_name']:
                    self.args['bytesprocessed'] = 0
                    self.args['currentfile'] = download_request['file_name']
                while True:
                    try:
                        input_file = requests.get(download_request['download_url'], stream=True).raw
                        break
                    except:
                        self.errqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-error': traceback.format_exc()})
                while self.args['partscompleted'] < len(download_request['chunks']):
                    try:
                        self.outqueue.put({'chunk_size': download_request['chunks'][self.args['partscompleted']][1],
                                           'chunk_start': download_request['chunks'][self.args['partscompleted']][0],
                                           'data': input_file.read(download_request['chunks'][self.args['partscompleted']][1])})
                        self.args['bytesprocessed'] += download_request['chunks'][self.args['partscompleted']][1]
                        self.args['partscompleted'] +=1
                    except:
                        self.errqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-error': traceback.format_exc()})
                self.args['partscompleted'] = 0
                self.inqueue.task_done()
        elif self.workertype == 'uploader':
            while True:
                try:
                    upload_request = self.inqueue.get()
                    if self.args['currentfile'] != upload_request['file_name']:
                        self.args['bytesprocessed'] = 0
                        self.args['currentfile'] = upload_request['file_name']
                        self.args['file_size'] = upload_request['file_size']
                    chunk = upload_request['chunks'][0]['data']
                    if len(upload_request['chunks']) > 1:
                        for c in xrange(1,len(upload_request['chunks'])):
                            chunk+=upload_request['chunks'][c]['data']
                    while True:
                        try:
                            self.args['output_file'] = requests.post(upload_request['ul_url'] + "/" + str(upload_request['chunks'][0]['chunk_stats'][0]), data=chunk, timeout=self.args['timeout'])
                            self.args['bytes_this_time'] = sum([x['chunk_stats'][1] for x in upload_request['chunks']])
                            self.args['targetoffset'] = upload_request['chunks'][len(upload_request['chunks'])-1]['chunk_stats'][0]+self.args['bytes_this_time']
                            self.args['bytesprocessed'] += self.args['bytes_this_time'] #may be wrong length of chunk
                            break
                        except:
                            self.errqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-error': traceback.format_exc()})
                    if self.args['output_file'].text != '':
                        self.altqueue.put({'finished_upload': 1, 'completion_file_handle': self.args['output_file'].text})
                    self.inqueue.task_done()
                except:
                    self.errqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-error': traceback.format_exc()})
        elif self.workertype == 'decrypter':
            while True:
                try:
                    decrypt_request = self.inqueue.get()
                    if 'meta_mac' in decrypt_request:
                        self.args = decrypt_request
                        self.args['targetoffset'] = 0
                        self.args['k_str'] = a32_to_str(self.args['k'])
                        self.args['counter'] = Counter.new(128, initial_value=((self.args['iv'][0] << 32) + self.args['iv'][1]) << 64)
                        self.args['aes'] = AES.new(self.args['k_str'], AES.MODE_CTR, counter=self.args['counter'])
                        self.args['mac_str'] = '\0' * 16
                        self.args['mac_encryptor'] = AES.new(self.args['k_str'], AES.MODE_CBC, self.args['mac_str'])
                        self.args['iv_str'] = a32_to_str([self.args['iv'][0], self.args['iv'][1], self.args['iv'][0], self.args['iv'][1]])
                        
                    else: #make sure we're on the next block in the chain, otherwise cache. This should never really be higher than 6-8 elements due to # of workers. consider caching as file when memory limit is reached. (recommend 100MB)
                        if decrypt_request['chunk_start'] == self.args['targetoffset']:
                            self.__decrypt(decrypt_request)
                            if len(self.cache) > 0: #process any we may have cached
                                while True:
                                    nextindex = [self.cache.index(x) for x in self.cache if x['chunk_start'] == self.args['targetoffset']]
                                    if len(nextindex) > 0:
                                        decrypt_request = self.cache.pop(nextindex[0])
                                        self.__decrypt(decrypt_request)
                                    else:
                                        break
                        else: 
                            self.cache.append(decrypt_request)
                            
                        if self.args['targetoffset'] == self.args['file_size']: #if decryption is completed, check file mac for successful download
                            self.args['file_mac'] = str_to_a32(self.args['mac_str'])
                            if (self.args['file_mac'][0] ^ self.args['file_mac'][1], self.args['file_mac'][2] ^ self.args['file_mac'][3]) == self.args['meta_mac']:
                                complete_code = True
                            else:
                                complete_code = False
                            self.outqueue.put({'complete_code': complete_code})
                    self.inqueue.task_done()
                except:
                    self.errqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-error': traceback.format_exc()})
        elif self.workertype == 'encrypter':
            while True:
                try:
                    encrypt_request = self.inqueue.get()
                    if 'ul_url' in encrypt_request:
                        self.args = encrypt_request
                        self.args['targetoffset'] = 0
                    elif 'finished_upload' in encrypt_request:
                        self.args['completion_file_handle'] = encrypt_request['completion_file_handle']
                        self.args['file_mac'] = str_to_a32(self.args['mac_str'])
                        self.args['meta_mac'] = (self.args['file_mac'][0] ^ self.args['file_mac'][1], self.args['file_mac'][2] ^ self.args['file_mac'][3])
                        self.args['encrypt_attribs'] = base64_url_encode(encrypt_attr(self.args['attribs'], self.args['ul_key'][:4]))
                        self.args['key'] = [self.args['ul_key'][0] ^ self.args['ul_key'][4], self.args['ul_key'][1] ^ self.args['ul_key'][5],
                                            self.args['ul_key'][2] ^ self.args['meta_mac'][0], self.args['ul_key'][3] ^ self.args['meta_mac'][1],
                                            self.args['ul_key'][4], self.args['ul_key'][5], self.args['meta_mac'][0], self.args['meta_mac'][1]]
                        self.args['encrypted_key'] = a32_to_base64(encrypt_key(self.args['key'], self.args['master_key']))
                        self.args['completed_response'] = self.parentmegaobject.api_request({'a': 'p', 't': self.args['dest'], 'n': [{
                        'h': self.args['completion_file_handle'],
                        't': 0,
                        'a': self.args['encrypt_attribs'],
                        'k': self.args['encrypted_key']}]})
                        self.args['completed_link'] = self.parentmegaobject.get_upload_link(self.args['completed_response'])
                    else: #make sure we're on the next block in the chain, otherwise cache. This should never really be higher than 6-8 elements due to # of workers. consider caching as file when memory limit is reached. (recommend 100MB)
                        if encrypt_request['chunks'][0]['chunk_stats'][0] == self.args['targetoffset']:
                            self.__encrypt(encrypt_request)
                            if len(self.cache) > 0: #process any we may have cached
                                while True:
                                    nextindex = [self.cache.index(x) for x in self.cache if x['chunks'][0]['chunk_stats'][0] == self.args['targetoffset']]
                                    if len(nextindex) > 0:
                                        encrypt_request = self.cache.pop(nextindex[0])
                                        self.__encrypt(encrypt_request)
                                    else:
                                        break
                        else: 
                            self.cache.append(encrypt_request)
                    self.inqueue.task_done()
                except:
                    self.errqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-error': traceback.format_exc()})
        elif self.workertype == 'writer':
            while True:
                try:
                    write_request = self.inqueue.get()
                    if 'temp_output_file' in write_request:
                        self.args = write_request
                        self.args['total_processed'] = 0
                    elif 'complete_code' in write_request:
                        self.args['temp_output_file'].close()
                        if write_request['complete_code'] == 1:
                            shutil.move(self.args['temp_output_file'].name, self.args['dest_path'] + self.args['file_name'])
                            message = 'verification successful! download complete.'
                        else:
                            os.remove(self.args['temp_output_file'].name)
                            message = 'file download failed verification. try again.'
                        self.msgqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-message': message})
                        
                    else:
                        self.args['temp_output_file'].write(write_request['data'])
                        self.args['total_processed'] += write_request['chunk_size']
                    self.inqueue.task_done()
                except:
                    self.errqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-error': traceback.format_exc()})
        elif self.workertype == 'reader':
            while True:
                try:
                    read_request = self.inqueue.get()
                    if 'input_file' in read_request:
                        self.args = read_request
                        self.args['total_processed'] = 0
                    else:
                        for i in xrange(0,len(read_request['chunks'])):
                            read_request['chunks'][i]['data'] = self.args['input_file'].read(read_request['chunks'][i]['chunk_stats'][1])
                            self.args['total_processed'] += read_request['chunks'][i]['chunk_stats'][1]
                        self.outqueue.put({'chunks': read_request['chunks'], 'filename': self.args['file_name']})
                        if self.args['total_processed'] == self.args['file_size']:
                            self.args['input_file'].close()
                    self.inqueue.task_done()
                except:
                    self.errqueue.put({'thread-type': self.workertype,'thread-args': self.args,'thread-error': traceback.format_exc()})
        else:
            print "self.workertype still not set. Shouldn't have gotten here."
        self.join()
        
    def __decrypt(self,decrypt_request):
        self.args['targetoffset'] += decrypt_request['chunk_size']
        chunk = self.args['aes'].decrypt(decrypt_request['data'])
        self.outqueue.put({'chunk_size': decrypt_request['chunk_size'],
                           'data': chunk})
        self.args['encryptor'] = AES.new(self.args['k_str'], AES.MODE_CBC, self.args['iv_str'])
        
        for i in range(0, len(chunk)-16, 16):
            block = chunk[i:i + 16]
            self.args['encryptor'].encrypt(block)
            
        #fix for files under 16 bytes failing
        if self.args['file_size'] > 16:
            i += 16
        else:
            i = 0
        
        #buffer to nearest boundary to hash
        block = chunk[i:i + 16]
        if len(block) % 16:
            block += '\0' * (16 - (len(block) % 16))
        self.args['mac_str'] = self.args['mac_encryptor'].encrypt(self.args['encryptor'].encrypt(block))
        return True

    def __encrypt(self,encrypt_request):
        for a in range(0,len(encrypt_request['chunks'])):
            self.args['encryptor'] = AES.new(self.args['k_str'], AES.MODE_CBC, self.args['iv_str'])
            for i in range(0, len(encrypt_request['chunks'][a]['data'])-16, 16):
                block = encrypt_request['chunks'][a]['data'][i:i + 16]
                self.args['encryptor'].encrypt(block)
                
            #fix for files under 16 bytes failing
            if self.args['file_size'] > 16:
                i += 16
            else:
                i = 0
                
            block = encrypt_request['chunks'][a]['data'][i:i + 16]
            if len(block) % 16:
                block += '\0' * (16 - len(block) % 16)
            self.args['mac_str'] = self.args['mac_encryptor'].encrypt(self.args['encryptor'].encrypt(block))
            encrypt_request['chunks'][a]['data'] = self.args['aes'].encrypt(encrypt_request['chunks'][a]['data'])
            self.args['targetoffset'] += encrypt_request['chunks'][a]['chunk_stats'][1] #size
        self.outqueue.put({'chunks': encrypt_request['chunks'],'ul_url': self.args['ul_url'], 'file_size': self.args['file_size'], 'file_name': self.args['file_name']})
        return True
    
class Mega(object):
    def __init__(self, options=None):
        self.schema = 'https'
        self.domain = 'mega.co.nz'
        self.timeout = 160  # max time (secs) to wait for resp from api requests
        self.sid = None
        self.sequence_num = random.randint(0, 0xFFFFFFFF)
        self.request_id = make_id(10)
        self.errorqueue = Queue.Queue()
        self.messagequeue = Queue.Queue()
        self.filedownloadqueue = Queue.Queue()
        self.fileuploadqueue = Queue.Queue()
        self.maxworkers = 6
        self.thisobj = self
        self.processors = self.__start_processors() #{downloader,uploader}
        if options is None:
            options = {}
        self.options = options

    def __start_processors(self):
        procs = {'download': None,'upload': None}
        
        #initialize download processor
        procs['download'] = Mega_Processor(filedownloadqueue=self.filedownloadqueue,messagequeue=self.messagequeue,errorqueue=self.errorqueue,megaobj=self.thisobj,args={'maxworkers': self.maxworkers})
        procs['download'].setDaemon(True)
        procs['download'].start()
        
        #initialize upload processor
        procs['upload'] = Mega_Processor(fileuploadqueue=self.fileuploadqueue,messagequeue=self.messagequeue,errorqueue=self.errorqueue,megaobj=self.thisobj,args={'maxworkers': self.maxworkers})
        procs['upload'].setDaemon(True)
        procs['upload'].start()
        
        return procs
    
    def login(self, email=None, password=None):
        if email:
            self.login_user(email, password)
        else:
            self.login_anonymous()
        return self

    def login_user(self, email, password):
        password_aes = prepare_key(str_to_a32(password))
        uh = stringhash(email, password_aes)
        resp = self.api_request({'a': 'us', 'user': email, 'uh': uh})
        #if numeric error code response
        if isinstance(resp, int):
            raise RequestError(resp)
        self._login_process(resp, password_aes)

    def login_anonymous(self):
        master_key = [random.randint(0, 0xFFFFFFFF)] * 4
        password_key = [random.randint(0, 0xFFFFFFFF)] * 4
        session_self_challenge = [random.randint(0, 0xFFFFFFFF)] * 4

        user = self.api_request({
          'a': 'up',
          'k': a32_to_base64(encrypt_key(master_key, password_key)),
          'ts': base64_url_encode(a32_to_str(session_self_challenge) +
                                  a32_to_str(encrypt_key(session_self_challenge, master_key)))
        })

        resp = self.api_request({'a': 'us', 'user': user})
        #if numeric error code response
        if isinstance(resp, int):
            raise RequestError(resp)
        self._login_process(resp, password_key)

    def _login_process(self, resp, password):
        encrypted_master_key = base64_to_a32(resp['k'])
        self.master_key = decrypt_key(encrypted_master_key, password)
        if 'tsid' in resp:
            tsid = base64_url_decode(resp['tsid'])
            key_encrypted = a32_to_str(
                encrypt_key(str_to_a32(tsid[:16]), self.master_key))
            if key_encrypted == tsid[-16:]:
                self.sid = resp['tsid']
        elif 'csid' in resp:
            encrypted_rsa_private_key = base64_to_a32(resp['privk'])
            rsa_private_key = decrypt_key(encrypted_rsa_private_key,
                                          self.master_key)

            private_key = a32_to_str(rsa_private_key)
            self.rsa_private_key = [0, 0, 0, 0]

            for i in range(4):
                l = ((ord(private_key[0]) * 256 + ord(private_key[1]) + 7) / 8) + 2
                self.rsa_private_key[i] = mpi_to_int(private_key[:l])
                private_key = private_key[l:]

            encrypted_sid = mpi_to_int(base64_url_decode(resp['csid']))
            rsa_decrypter = RSA.construct(
                (self.rsa_private_key[0] * self.rsa_private_key[1],
                 0L, self.rsa_private_key[2], self.rsa_private_key[0],
                 self.rsa_private_key[1]))

            sid = '%x' % rsa_decrypter.key._decrypt(encrypted_sid)
            sid = binascii.unhexlify('0' + sid if len(sid) % 2 else sid)
            self.sid = base64_url_encode(sid[:43])

    def api_request(self, data):
        params = {'id': self.sequence_num}
        self.sequence_num += 1

        if self.sid:
            params.update({'sid': self.sid})

        #ensure input data is a list
        if not isinstance(data, list):
            data = [data]

        req = requests.post(
            '{0}://g.api.{1}/cs'.format(self.schema, self.domain),
            params=params,
            data=json.dumps(data),
            timeout=self.timeout)
        json_resp = json.loads(req.text)
        #if numeric error code response
        if isinstance(json_resp, int):
            raise RequestError(json_resp)
        return json_resp[0]

    def parse_url(self, url):
        #parse file id and key from url
        if ('!' in url):
            match = re.findall(r'/#!(.*)', url)
            path = match[0]
            return path
        else:
            raise RequestError('Url key missing')

    def process_file(self, file, shared_keys):
        """
        Process a file
        """
        if file['t'] == 0 or file['t'] == 1:
            keys = dict(keypart.split(':', 1) for keypart in file['k'].split('/') if ':' in keypart)
            uid = file['u']
            key = None
            # my objects
            if uid in keys:
                key = decrypt_key(base64_to_a32(keys[uid]), self.master_key)
            # shared folders 
            elif 'su' in file and 'sk' in file and ':' in file['k']:
                shared_key = decrypt_key(base64_to_a32(file['sk']), self.master_key)
                key = decrypt_key(base64_to_a32(keys[file['h']]), shared_key)
                if file['su'] not in shared_keys:
                    shared_keys[file['su']] = {}
                shared_keys[file['su']][file['h']] = shared_key
            # shared files
            elif file['u'] and file['u'] in shared_keys:
                for hkey in shared_keys[file['u']]:
                    shared_key = shared_keys[file['u']][hkey]
                    if hkey in keys:
                        key = keys[hkey]
                        key = decrypt_key(base64_to_a32(key), shared_key)
                        break
            if key is not None:
                # file
                if file['t'] == 0:
                    k = (key[0] ^ key[4], key[1] ^ key[5], key[2] ^ key[6],
                         key[3] ^ key[7])
                    file['iv'] = key[4:6] + (0, 0)
                    file['meta_mac'] = key[6:8]
                # folder
                else:
                    k = key
                file['key'] = key
                file['k'] = k
                attributes = base64_url_decode(file['a'])
                attributes = decrypt_attr(attributes, k)
                file['a'] = attributes
            # other => wrong object
            elif file['k'] == '':
                file['a'] = False
        elif file['t'] == 2:
            self.root_id = file['h']
            file['a'] = {'n': 'Cloud Drive'}
        elif file['t'] == 3:
            self.inbox_id = file['h']
            file['a'] = {'n': 'Inbox'}
        elif file['t'] == 4:
            self.trashbin_id = file['h']
            file['a'] = {'n': 'Rubbish Bin'}
        return file

    def init_shared_keys(self, files, shared_keys):
        """
        Init shared key not associated with a user.
        Seems to happen when a folder is shared,
        some files are exchanged and then the
        folder is un-shared.
        Keys are stored in files['s'] and files['ok']
        """
        ok_dict = {}
        for ok_item in files['ok']:
            shared_key = decrypt_key(base64_to_a32(ok_item['k']), self.master_key)
            ok_dict[ok_item['h']] = shared_key
        for s_item in files['s']:
            if s_item['u'] not in shared_keys:
                shared_keys[s_item['u']] = {}
            if s_item['h'] in ok_dict:
                shared_keys[s_item['u']][s_item['h']] = ok_dict[s_item['h']]

    ##########################################################################
    # GET
    def find(self, filename):
        """
        Return file object from given filename
        """
        files = self.get_files()
        for file in files.items():
            if file[1]['a'] and file[1]['a']['n'] == filename:
                return file

    def get_files(self):
        """
        Get all files in account
        """
        files = self.api_request({'a': 'f', 'c': 1})
        files_dict = {}
        shared_keys = {}
        self.init_shared_keys(files, shared_keys)
        for file in files['f']:
            processed_file = self.process_file(file, shared_keys)
            #ensure each file has a name before returning
            if processed_file['a']:
                files_dict[file['h']] = processed_file
        return files_dict

    def get_upload_link(self, file):
        """
        Get a files public link inc. decrypted key
        Requires upload() response as input
        """
        if 'f' in file:
            file = file['f'][0]
            public_handle = self.api_request({'a': 'l', 'n': file['h']})
            file_key = file['k'][file['k'].index(':') + 1:]
            decrypted_key = a32_to_base64(decrypt_key(base64_to_a32(file_key),
                                                      self.master_key))
            return '{0}://{1}/#!{2}!{3}'.format(self.schema,
                                                self.domain,
                                                public_handle,
                                                decrypted_key)
        else:
            raise ValueError('''Upload() response required as input,
                            use get_link() for regular file input''')

    def get_link(self, file):
        """
        Get a file public link from given file object
        """
        file = file[1]
        if 'h' in file and 'k' in file:
            public_handle = self.api_request({'a': 'l', 'n': file['h']})
            if public_handle == -11:
                raise RequestError("Can't get a public link from that file (is this a shared file?)")
            decrypted_key = a32_to_base64(file['key'])
            return '{0}://{1}/#!{2}!{3}'.format(self.schema,
                                                self.domain,
                                                public_handle,
                                                decrypted_key)
        else:
            raise ValidationError('File id and key must be present')

    def get_user(self):
        user_data = self.api_request({'a': 'ug'})
        return user_data

    def get_node_by_type(self, type):
        """
        Get a node by it's numeric type id, e.g:
        0: file
        1: dir
        2: special: root cloud drive
        3: special: inbox
        4: special trash bin
        """
        nodes = self.get_files()
        for node in nodes.items():
            if (node[1]['t'] == type):
                return node

    def get_files_in_node(self, target):
        """
        Get all files in a given target, e.g. 4=trash
        """
        if type(target) == int:
            # convert special nodes (e.g. trash)
            node_id = self.get_node_by_type(target)
        else:
            node_id = [target]

        files = self.api_request({'a': 'f', 'c': 1})
        files_dict = {}
        shared_keys = {}
        self.init_shared_keys(files, shared_keys)
        for file in files['f']:
            processed_file = self.process_file(file, shared_keys)
            if processed_file['a'] and processed_file['p'] == node_id[0]:
                files_dict[file['h']] = processed_file
        return files_dict

    def get_id_from_public_handle(self, public_handle):
        #get node data
        node_data = self.api_request({'a': 'f', 'f': 1, 'p': public_handle})
        node_id = self.get_id_from_obj(node_data)
        return node_id

    def get_id_from_obj(self, node_data):
        """
        Get node id from a file object
        """
        node_id = None

        for i in node_data['f']:
            if i['h'] is not u'':
                node_id = i['h']
        return node_id

    def get_quota(self):
        """
        Get current remaining disk quota in MegaBytes
        """
        json_resp = self.api_request({'a': 'uq', 'xfer': 1})
        #convert bytes to megabyes
        return json_resp['mstrg'] / 1048576

    def get_storage_space(self, giga=False, mega=False, kilo=False):
        """
        Get the current storage space.
        Return a dict containing at least:
          'used' : the used space on the account
          'total' : the maximum space allowed with current plan
        All storage space are in bytes unless asked differently.
        """
        if sum(1 if x else 0 for x in (kilo, mega, giga)) > 1:
            raise ValueError("Only one unit prefix can be specified")
        unit_coef = 1
        if kilo:
            unit_coef = 1024
        if mega:
            unit_coef = 1048576
        if giga:
            unit_coef = 1073741824
        json_resp = self.api_request({'a': 'uq', 'xfer': 1, 'strg': 1})
        return {
            'used': json_resp['cstrg'] / unit_coef,
            'total': json_resp['mstrg'] / unit_coef,
        }

    def get_balance(self):
        """
        Get account monetary balance, Pro accounts only
        """
        user_data = self.api_request({"a": "uq", "pro": 1})
        if 'balance' in user_data:
            return user_data['balance']

    ##########################################################################
    # DELETE
    def delete(self, public_handle):
        """
        Delete a file by its public handle
        """
        return self.move(public_handle, 4)

    def delete_url(self, url):
        """
        Delete a file by its url
        """
        path = self.parse_url(url).split('!')
        public_handle = path[0]
        file_id = self.get_id_from_public_handle(public_handle)
        return self.move(file_id, 4)

    def destroy(self, file_id):
        """
        Destroy a file by its private id
        """
        return self.api_request({'a': 'd',
                                 'n': file_id,
                                 'i': self.request_id})

    def destroy_url(self, url):
        """
        Destroy a file by its url
        """
        path = self.parse_url(url).split('!')
        public_handle = path[0]
        file_id = self.get_id_from_public_handle(public_handle)
        return self.destroy(file_id)

    def empty_trash(self):
        # get list of files in rubbish out
        files = self.get_files_in_node(4)

        # make a list of json
        if files != {}:
            post_list = []
            for file in files:
                post_list.append({"a": "d",
                                  "n": file,
                                  "i": self.request_id})
            return self.api_request(post_list)

    ##########################################################################
    # DOWNLOAD
    def download(self, file, dest_path=None, dest_filename=None):
        """
        Download a file by it's file object
        """
        self.download_file(None, None, file=file[1], dest_path=dest_path, dest_filename=dest_filename, is_public=False)

    def download_url(self, url, dest_path=None, dest_filename=None):
        """
        Download a file by it's public url
        """
        path = self.parse_url(url).split('!')
        file_id = path[0]
        file_key = path[1]
        self.download_file(file_id, file_key, dest_path, dest_filename, is_public=True)

    def download_file(self, file_handle, file_key, dest_path=None, dest_filename=None, is_public=False, file=None):
        ##INITIALIZE
        if file is None :
            if is_public:
                file_key = base64_to_a32(file_key)
                file_data = self.api_request({'a': 'g', 'g': 1, 'p': file_handle})
            else:
                file_data = self.api_request({'a': 'g', 'g': 1, 'n': file_handle})

            k = (file_key[0] ^ file_key[4], file_key[1] ^ file_key[5],
                 file_key[2] ^ file_key[6], file_key[3] ^ file_key[7])
            iv = file_key[4:6] + (0, 0)
            meta_mac = file_key[6:8]
        else:
            file_data = self.api_request({'a': 'g', 'g': 1, 'n': file['h']})
            k = file['k']
            iv = file['iv']
            meta_mac = file['meta_mac']

        # Seems to happens sometime... When  this occurs, files are 
        # inaccessible also in the official also in the official web app.
        # Strangely, files can come back later.
        if 'g' not in file_data:
            raise RequestError('File not accessible anymore')
        file_url = file_data['g']
        file_size = file_data['s']
        attribs = base64_url_decode(file_data['at'])
        attribs = decrypt_attr(attribs, k)

        if dest_filename is not None:
            file_name = dest_filename
        else:
            file_name = attribs['n']
        
        if dest_path is None:
            dest_path = ''
        else:
            dest_path += '/'
        
        temp_output_file = tempfile.NamedTemporaryFile(mode='w+b', prefix='megapy_', delete=False)
        downloadobj = {
            'k': k,
            'iv': iv,
            'meta_mac': meta_mac,
            'file_data': file_data,
            'file_url': file_url,
            'file_size': file_size,
            'file_name': file_name,
            'dest_path': dest_path,
            'temp_output_file': temp_output_file
        }
        self.filedownloadqueue.put(downloadobj)

    def download_status(self):
        statusobj = {}
        statusobj['downloadedbytes'] = sum([x.args['bytesprocessed'] for x in self.processors['download'].workers['downloader']])
        statusobj['writtenbytes'] = self.processors['download'].workers['writer'][0].args['total_processed']
        statusobj['totalbytes'] = self.processors['download'].currentfile['file_size']
        return statusobj
    
    def upload_status(self):
        statusobj = {}
        statusobj['uploadedbytes'] = sum([x.args['bytesprocessed'] for x in self.processors['upload'].workers['uploader']])
        statusobj['readbytes'] = self.processors['upload'].workers['reader'][0].args['total_processed']
        statusobj['totalbytes'] = self.processors['upload'].currentfile['file_size']
        return statusobj
    ##########################################################################
    # UPLOAD
    def upload(self, filename, dest=None, dest_filename=None):
        #determine storage node
        if dest is None:
            #if none set, upload to cloud drive node
            if not hasattr(self, 'root_id'):
                self.get_files()
            dest = self.root_id

        #request upload url, call 'u' method
        input_file = open(filename, 'rb')
        file_size = os.path.getsize(filename)
        ul_url = self.api_request({'a': 'u', 's': file_size})['p']

        #generate random aes key (128) for file
        ul_key = [random.randint(0, 0xFFFFFFFF) for _ in range(6)]
        k_str = a32_to_str(ul_key[:4])
        count = Counter.new(128, initial_value=((ul_key[4] << 32) + ul_key[5]) << 64)
        aes = AES.new(k_str, AES.MODE_CTR, counter=count)

        upload_progress = 0
        completion_file_handle = None

        mac_str = '\0' * 16
        mac_encryptor = AES.new(k_str, AES.MODE_CBC, mac_str)
        iv_str = a32_to_str([ul_key[4], ul_key[5], ul_key[4], ul_key[5]])
        
        fname = None
        if dest_filename is not None:
            attribs = {'n': dest_filename}
            fname = dest_filename
        else:
            attribs = {'n': os.path.basename(filename)}
            fname = os.path.basename(filename)

        uploadobj = {
            'input_file': input_file,
            'dest' : dest,
            'dest_filename': attribs['n'],
            'file_name': fname,
            'file_size': file_size,
            'ul_url': ul_url,
            'ul_key': ul_key,
            'k_str': k_str,
            'count': count,
            'aes': aes,
            'completion_file_handle': completion_file_handle,
            'mac_str': mac_str,
            'mac_encryptor': mac_encryptor,
            'iv_str': iv_str,
            'attribs': attribs,
            'master_key': self.master_key
                }
        
        self.fileuploadqueue.put(uploadobj)

    ##########################################################################
    # OTHER OPERATIONS
    def create_folder(self, name, dest=None):
        #determine storage node
        if dest is None:
            #if none set, upload to cloud drive node
            if not hasattr(self, 'root_id'):
                self.get_files()
            dest = self.root_id

        #generate random aes key (128) for folder
        ul_key = [random.randint(0, 0xFFFFFFFF) for _ in range(6)]

        #encrypt attribs
        attribs = {'n': name}
        encrypt_attribs = base64_url_encode(encrypt_attr(attribs, ul_key[:4]))
        encrypted_key = a32_to_base64(encrypt_key(ul_key[:4], self.master_key))

        #update attributes
        data = self.api_request({'a': 'p',
                                 't': dest,
                                 'n': [{
                                     'h': 'xxxxxxxx',
                                     't': 1,
                                     'a': encrypt_attribs,
                                     'k': encrypted_key}
                                 ],
                                 'i': self.request_id})
        #return API msg
        return data

    def rename(self, file, new_name):
        file = file[1]
        #create new attribs
        attribs = {'n': new_name}
        #encrypt attribs
        encrypt_attribs = base64_url_encode(encrypt_attr(attribs, file['k']))
        encrypted_key = a32_to_base64(encrypt_key(file['key'], self.master_key))

        #update attributes
        data = self.api_request([{
            'a': 'a',
            'attr': encrypt_attribs,
            'key': encrypted_key,
            'n': file['h'],
            'i': self.request_id}])

        #return API msg
        return data

    def move(self, file_id, target):
        """
        Move a file to another parent node
        params:
        a : command
        n : node we're moving
        t : id of target parent node, moving to
        i : request id

        targets
        2 : root
        3 : inbox
        4 : trash

        or...
        target's id
        or...
        target's structure returned by find()
        """

        #determine target_node_id
        if type(target) == int:
            target_node_id = str(self.get_node_by_type(target)[0])
        elif type(target) in (str, unicode):
            target_node_id = target
        else:
            file = target[1]
            target_node_id = file['h']
        return self.api_request({'a': 'm',
                                 'n': file_id,
                                 't': target_node_id,
                                 'i': self.request_id})

    def add_contact(self, email):
        """
        Add another user to your mega contact list
        """
        return self._edit_contact(email, True)

    def remove_contact(self, email):
        """
        Remove a user to your mega contact list
        """
        return self._edit_contact(email, False)

    def _edit_contact(self, email, add):
        """
        Editing contacts
        """
        if add is True:
            l = '1'  # add command
        elif add is False:
            l = '0'  # remove command
        else:
            raise ValidationError('add parameter must be of type bool')

        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            ValidationError('add_contact requires a valid email address')
        else:
            return self.api_request({'a': 'ur',
                                    'u': email,
                                    'l': l,
                                    'i': self.request_id})

    def get_contacts(self):
        raise NotImplementedError()
        # TODO implement this
        # sn param below = maxaction var with function getsc() in mega.co.nz js
        # seens to be the 'sn' attrib of the previous request response...
        # mega.co.nz js full source @ http://homepages.shu.ac.uk/~rjodwyer/mega-scripts-all.js
        # requests goto /sc rather than

        #req = requests.post(
        #'{0}://g.api.{1}/sc'.format(self.schema, self.domain),
        #    params={'sn': 'ZMxcQ_DmHnM', 'ssl': '1'},
        #    data=json.dumps(None),
        #    timeout=self.timeout)
        #json_resp = json.loads(req.text)
        #print json_resp
    
    def get_public_url_info(self, url):
        """
        Get size and name from a public url, dict returned
        """
        file_handle, file_key = self.parse_url(url).split('!')
        return self.get_public_file_info(file_handle, file_key)

    def import_public_url(self, url, dest_node=None, dest_name=None):
        """
        Import the public url into user account
        """
        file_handle, file_key = self.parse_url(url).split('!')
        return self.import_public_file(file_handle, file_key, dest_node=dest_node, dest_name=dest_name)

    def get_public_file_info(self, file_handle, file_key):
        """
        Get size and name of a public file
        """
        data = self.api_request({
            'a': 'g',
            'p': file_handle,
            'ssm': 1})

        #if numeric error code response
        if isinstance(data, int):
            raise RequestError(data)

        if 'at' not in data or 's' not in data:
            raise ValueError("Unexpected result", data)

        key = base64_to_a32(file_key)
        k = (key[0] ^ key[4], key[1] ^ key[5], key[2] ^ key[6], key[3] ^ key[7])

        size = data['s']
        unencrypted_attrs = decrypt_attr(base64_url_decode(data['at']), k)
        if not(unencrypted_attrs):
            return None

        result = {
            'size': size,
            'name': unencrypted_attrs['n']}

        return result

    def import_public_file(self, file_handle, file_key, dest_node=None, dest_name=None):
        """
        Import the public file into user account
        """

        # Providing dest_node spare an API call to retrieve it.
        if dest_node is None:
            # Get '/Cloud Drive' folder no dest node specified
            dest_node = self.get_node_by_type(2)[1]

        # Providing dest_name spares an API call to retrieve it.
        if dest_name is None:
            pl_info = self.get_public_file_info(file_handle, file_key)
            dest_name = pl_info['name']

        key = base64_to_a32(file_key)
        k = (key[0] ^ key[4], key[1] ^ key[5], key[2] ^ key[6], key[3] ^ key[7])

        encrypted_key = a32_to_base64(encrypt_key(key, self.master_key))
        encrypted_name = base64_url_encode(encrypt_attr({'n': dest_name}, k))

        data = self.api_request({
            'a': 'p',
            't': dest_node['h'],
            'n': [{
                'ph': file_handle,
                't': 0,
                'a': encrypted_name,
                'k': encrypted_key}]})

        #return API msg
        return data
