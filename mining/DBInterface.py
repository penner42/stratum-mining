from twisted.internet import reactor, defer
import time
from datetime import datetime
import Queue
import signal
import Cache
from sets import Set

import lib.settings as settings

import lib.logger
log = lib.logger.get_logger('DBInterface')

class DBInterface():
    def __init__(self):
        self.dbi = self.connectDB()

    # TODO get rid of this, and put it in __init__?
    def init_main(self):
        self.dbi.check_tables()
        self.q = Queue.Queue()
        self.queueclock = None
        self.cache = Cache.Cache()
        self.nextStatsUpdate = 0
        self.scheduleImport()
        self.next_force_import_time = time.time() + settings.DB_LOADER_FORCE_TIME
        self.import_in_progress = False
        signal.signal(signal.SIGINT, self.signal_handler)

    def signal_handler(self, signal, frame):
        log.warning("SIGINT Detected, shutting down")
        self.do_import(self.dbi, True)
        reactor.stop()

    def set_bitcoinrpc(self, bitcoinrpc):
        self.bitcoinrpc = bitcoinrpc

    def connectDB(self):
        log.debug('DB_Mysql INIT')
        import DB_Mysql
        return DB_Mysql.DB_Mysql()

    def scheduleImport(self):
        # this schedules the import
        self.queueclock = reactor.callLater(settings.DB_LOADER_CHECKTIME , self.run_import)
    
    def run_import(self):
        log.debug("DBInterface.run_import called")
        self.do_import(self.dbi, False)
        self.scheduleImport()

    def wait_for_import(self):
        if self.import_in_progress:
            log.info("waiting for import to finish...")
            return self.d
        else:
            return defer.succeed(True)

    @defer.inlineCallbacks
    def do_import(self, dbi, force):
        log.debug("DBInterface.do_import called. force: %s, queue size: %s", 'yes' if force is True else 'no',
                  self.q.qsize())

        if self.import_in_progress:
            # import already in progress; don't enter again
            return

        self.import_in_progress = True
        self.d = defer.Deferred()

        # Only run if we have data
        while not self.q.empty():
            # Put together the data we want to import
            sqldata = []
            datacnt = 0
            
            while not self.q.empty() and datacnt < settings.DB_LOADER_REC_MAX:
                datacnt += 1
                data = self.q.get()
                sqldata.append(data)
                self.q.task_done()

            # try to do the import, if we fail, log the error and put the data back in the queue
            try:
                log.info("Inserting %s Share Records", datacnt)
                res = yield dbi.import_shares(sqldata)
            except Exception as e:
                log.error("Insert Share Records Failed: %s", e.args[0])
                for k, v in enumerate(sqldata):
                    self.q.put(v)

        self.import_in_progress = False
        self.d.callback(True)

    def queue_share(self, data):
        self.q.put(data)

    def found_block(self, data):
        try:
            log.info("Updating Found Block Share Record")
            self.do_import(self.dbi, True)  # can't update if the record is not there.
            d = self.wait_for_import()      # wait for import to finish before updating block
            d.addCallback(self.dbi.found_block, data)
        except Exception as e:
            log.error("Update Found Block Share Record Failed: %s", e.args[0])

    @defer.inlineCallbacks
    def check_password(self, username, password):
        if username == "":
            log.info("Rejected worker for blank username")
            defer.returnValue(False)
        allowed_chars = Set('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-.')
        if Set(username).issubset(allowed_chars) != True:
            log.info("Username contains bad arguments")
            defer.returnValue(False)
        if username.count('.') > 1:
            log.info("Username contains multiple . ")
            defer.returnValue(False)
        
        # Force username and password to be strings
        username = str(username)
        password = str(password)
        if not settings.USERS_CHECK_PASSWORD and (yield self.user_exists(username)):
            defer.returnValue(True)
        elif self.cache.get(username) == password:
            defer.returnValue(True)
        elif (yield defer.maybeDeferred(self.dbi.check_password, username, password)):
            self.cache.set(username, password)
            defer.returnValue(True)
        elif settings.USERS_AUTOADD == True:
            uid = yield defer.maybeDeferred(self.dbi.get_uid, username)
            if uid != False:
                self.dbi.insert_worker(uid, username, password)
                self.cache.set(username, password)
                defer.returnValue(True)
        
        log.info("Authentication for %s failed" % username)
        defer.returnValue(False)
    
    # def list_users(self):
    #     return self.dbi.list_users()

    @defer.inlineCallbacks
    def get_user_nb(self, id):
        if self.cache.get(id) is None:
            user = yield defer.maybeDeferred(self.dbi.get_user_nb, id)
            self.cache.set(id, user)
        user = self.cache.get(id)
        defer.returnValue(user)

    @defer.inlineCallbacks
    def get_user(self, id):
        if self.cache.get(id) is None:
            log.debug("%s not in cache" % id)
            user = yield defer.maybeDeferred(self.dbi.get_user, id)
            ret = self.cache.set(id, user)
        defer.returnValue(self.cache.get(id))

    @defer.inlineCallbacks
    def user_exists(self, username):
        if self.cache.get(username) is not None:
            defer.returnValue(True)
        user = yield self.get_user(username)
        defer.returnValue(user is not None)

    def insert_user(self, username, password):        
        return self.dbi.insert_user(username, password)

    def update_worker_diff(self, username, diff):
        return self.dbi.update_worker_diff(username, diff)

    def get_workers_stats(self):
        return self.dbi.get_workers_stats()

    def clear_worker_diff(self):
        return self.dbi.clear_worker_diff()

