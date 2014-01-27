'''This module contains classes used by pool core to interact with the rest of the pool.
   Default implementation do almost nothing, you probably want to override these classes
   and customize references to interface instances in your launcher.
   (see launcher_demo.tac for an example).
''' 
import time
from twisted.internet import reactor, defer
from lib.util import b58encode

import lib.settings as settings
import lib.logger
log = lib.logger.get_logger('interfaces')

import DBInterface
dbi = DBInterface.DBInterface()
dbi.init_main()

class WorkerManagerInterface(object):
    def __init__(self):
        self.worker_log = {}
        self.worker_log.setdefault('authorized', {})
        self.job_log = {}
        self.job_log.setdefault('None', {})
        return
        
    def authorize(self, worker_name, worker_password):
        # Important NOTE: This is called on EVERY submitted share. So you'll need caching!!!
        return dbi.check_password(worker_name, worker_password)
 
    def get_user_difficulty(self, worker_name):
        wd = dbi.get_user(worker_name)
        if len(wd) > 6:
            if wd[6] != 0:
                return (True, wd[6])
                #dbi.update_worker_diff(worker_name, wd[6])
        return (False, settings.POOL_TARGET)

    def register_work(self, worker_name, job_id, difficulty):
        now = Interfaces.timestamper.time()
        work_id = WorkIdGenerator.get_new_id()
        self.job_log.setdefault(worker_name, {})[work_id] = (job_id, difficulty, now)
        return work_id

class WorkIdGenerator(object):
    counter = 1000
    
    @classmethod
    def get_new_id(cls):
        cls.counter += 1
        if cls.counter % 0xffff == 0:
            cls.counter = 1
        return "%x" % cls.counter

class ShareLimiterInterface(object):
    '''Implement difficulty adjustments here'''
    
    def submit(self, connection_ref, job_id, current_difficulty, timestamp, worker_name):
        '''connection - weak reference to Protocol instance
           current_difficulty - difficulty of the connection
           timestamp - submission time of current share
           
           - raise SubmitException for stop processing this request
           - call mining.set_difficulty on connection to adjust the difficulty'''
        #return dbi.update_worker_diff(worker_name, settings.POOL_TARGET)
        return
 
class ShareManagerInterface(object):
    def __init__(self):
        self.block_height = 0
        self.prev_hash = 0
    
    def on_network_block(self, prevhash, block_height):
        '''Prints when there's new block coming from the network (possibly new round)'''
        self.block_height = block_height        
        self.prev_hash = b58encode(int(prevhash, 16))
        pass
    
    def on_submit_share(self, worker_name, block_header, block_hash, difficulty, timestamp, is_valid, ip, invalid_reason, share_diff):
        log.debug("%s (%s) %s %s" % (block_hash, share_diff, 'valid' if is_valid else 'INVALID', worker_name))
        dbi.queue_share([worker_name, block_header, block_hash, difficulty, timestamp, is_valid, ip, self.block_height, self.prev_hash,
                invalid_reason, share_diff ])
 
    def on_submit_block(self, is_accepted, worker_name, block_header, block_hash, timestamp, ip, share_diff):
        log.info("Block %s %s" % (block_hash, 'ACCEPTED' if is_accepted else 'REJECTED'))
        dbi.found_block([worker_name, block_header, block_hash, -1, timestamp, is_accepted, ip, self.block_height, self.prev_hash, share_diff ])
        
class TimestamperInterface(object):
    '''This is the only source for current time in the application.
    Override this for generating unix timestamp in different way.'''
    def time(self):
        return time.time()

class PredictableTimestamperInterface(TimestamperInterface):
    '''Predictable timestamper may be useful for unit testing.'''
    start_time = 1345678900  # Some day in year 2012
    delta = 0
    
    def time(self):
        self.delta += 1
        return self.start_time + self.delta

class Interfaces(object):
    worker_manager = None
    share_manager = None
    share_limiter = None
    timestamper = None
    template_registry = None

    @classmethod
    def set_worker_manager(cls, manager):
        cls.worker_manager = manager    
    
    @classmethod        
    def set_share_manager(cls, manager):
        cls.share_manager = manager

    @classmethod        
    def set_share_limiter(cls, limiter):
        cls.share_limiter = limiter
    
    @classmethod
    def set_timestamper(cls, manager):
        cls.timestamper = manager
        
    @classmethod
    def set_template_registry(cls, registry):
        dbi.set_bitcoinrpc(registry.bitcoin_rpc)
        cls.template_registry = registry

    @classmethod
    @defer.inlineCallbacks
    def changeCoin(cls, host, port, user, password, address, powpos, txcomments):

        settings.COINDAEMON_TX = 'yes' if txcomments else 'no'
        log.info("CHANGING COIN # "+str(user)+" txcomments: "+settings.COINDAEMON_TX)
        
        ''' Function to add a litecoind instance live '''
        from lib.coinbaser import SimpleCoinbaser
        from lib.template_registry import TemplateRegistry
        from lib.block_template import BlockTemplate
        from lib.block_updater import BlockUpdater
        from subscription import MiningSubscription
        
        #(host, port, user, password) = args
        cls.template_registry.bitcoin_rpc.change_connection(str(host), port, str(user), str(password))

        result = (yield cls.template_registry.bitcoin_rpc.getblocktemplate())
        if isinstance(result, dict):
            # litecoind implements version 1 of getblocktemplate
            if result['version'] >= 1:
                result = (yield cls.template_registry.bitcoin_rpc.getdifficulty())
                if isinstance(result,dict):
                    if 'proof-of-stake' in result:
                        settings.COINDAEMON_Reward = 'POS'
                        log.info("Coin detected as POS")
                else:
                    settings.COINDAEMON_Reward = 'POW'
                    log.info("Coin detected as POW")
            else:
                    log.error("Block Version mismatch: %s" % result['version'])

        cls.template_registry.coinbaser.change(address)
        (yield cls.template_registry.coinbaser.on_load)
        
        cls.template_registry.update(BlockTemplate,
                                            cls.template_registry.coinbaser,
                                            cls.template_registry.bitcoin_rpc,
                                            31,
                                            MiningSubscription.on_template,
                                            cls.share_manager.on_network_block)
        
        result = (yield cls.template_registry.bitcoin_rpc.check_submitblock())
        if result == True:
            log.info("Found submitblock")
        elif result == False:
            log.info("Did not find submitblock")
        else:
            log.info("unknown submitblock result")
            
        (yield cls.template_registry.update_block())
        log.info("New litecoind connection changed %s:%s" % (host, port))
            
