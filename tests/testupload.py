from pylibmega import Mega
from pylibmega.crypto import *
import time
import datetime
import os
def test():
    #user details - test account
    email = ''
    password = ''

    mega = Mega()
    mega = Mega({'verbose': True})  # verbose option for print output

    # login
    print 'logging in'
    m = mega.login(email, password)

    # get user details
    print 'getting details'
    details = m.get_user()
    
    mega.upload('test3.rar')
    start = datetime.datetime.now()
    time.sleep(1)
    stats = mega.upload_status()
    #while stats['uploadedbytes'] < stats['totalbytes']:
    while True:
        os.system(['clear','cls'][os.name == 'nt'])
        totaltime = datetime.datetime.now()-start
        timesecs = totaltime.total_seconds()
        stats = mega.upload_status()
        urate = sizeof_fmt(int(stats['uploadedbytes']/timesecs))
        rrate = sizeof_fmt(int(stats['readbytes']/timesecs))
        print '(u)percent:'+str(float(stats['uploadedbytes'])/stats['totalbytes']*100)+'%\n(u)rate:'+str(urate)+'/s\n(r)percent:'+str(float(stats['readbytes'])/stats['totalbytes']*100)+'%\n(r)rate:'+str(rrate)+'/s'
        time.sleep(1)


if __name__ == '__main__':
    test()