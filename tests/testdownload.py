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
    
    start = datetime.datetime.now()
    time.sleep(1)
    stats = mega.download_status()
    while True:
        os.system(['clear','cls'][os.name == 'nt'])
        totaltime = datetime.datetime.now()-start
        timesecs = totaltime.total_seconds()
        stats = mega.download_status()
        drate = sizeof_fmt(int(stats['downloadedbytes']/timesecs))
        wrate = sizeof_fmt(int(stats['writtenbytes']/timesecs))
        print '(d)percent:'+str(float(stats['downloadedbytes'])/stats['totalbytes']*100)+'%\n(d)rate:'+str(drate)+'/s\n(w)percent:'+str(float(stats['writtenbytes'])/stats['totalbytes']*100)+'%\n(w)rate:'+str(wrate)+'/s'
        time.sleep(1)


if __name__ == '__main__':
    test()