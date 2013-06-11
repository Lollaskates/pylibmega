from pylibmega import Mega
from pylibmega.crypto import *
from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Util import Counter
import re
import tempfile
import os
import random
import binascii
import requests
import time
import shutil
import datetime
import os

print 'initializing environment...'
#use 1MB chunks (standard mega format)
chunksize = 0x1000000
filedata = os.urandom(chunksize)
total_iterations = 0

#generate random aes key (128) for file
ul_key = [random.randint(0, 0xFFFFFFFF) for _ in range(6)]
k_str = a32_to_str(ul_key[:4])
count = Counter.new(128, initial_value=((ul_key[4] << 32) + ul_key[5]) << 64)
aes = AES.new(k_str, AES.MODE_CTR, counter=count)
mac_str = '\0' * 16
mac_encryptor = AES.new(k_str, AES.MODE_CBC, mac_str)
iv_str = a32_to_str([ul_key[4], ul_key[5], ul_key[4], ul_key[5]])



def encrypt_test():
    global chunksize, filedata, total_iterations, ul_key, k_str, count, aes, mac_str, mac_encryptor, iv_str
    
    encryptor = AES.new(k_str, AES.MODE_CBC, iv_str)
    for i in range(0, len(filedata)-16, 16):
        block = filedata[i:i + 16]
        encryptor.encrypt(block)
        
    block = filedata[i:i + 16]
    if len(block) % 16:
        block += '\0' * (16 - len(block) % 16)
    mac_str = mac_encryptor.encrypt(encryptor.encrypt(block))
    filedata = aes.encrypt(filedata)
    total_iterations += 1
    return True

def test():    
    start = datetime.datetime.now()
    while True:
        encrypt_test()
        os.system(['clear','cls'][os.name == 'nt'])
        totaltime = datetime.datetime.now()-start
        timesecs = totaltime.total_seconds()
        
        erate = sizeof_fmt(int(total_iterations/timesecs*chunksize))
        print '(e)rate:'+str(erate)+'/s'


if __name__ == '__main__':
    test()