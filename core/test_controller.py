# Copyright (C) 2009-2010 Raul Jimenez
# Released under GNU LGPL 2.1
# See LICENSE.txt for more information

import logging

from nose.tools import ok_, eq_

import ptime as time
import test_const as tc
import message
import querier
import identifier

import controller

import routing_plugin_template as routing_m_mod
import lookup_plugin_template as lookup_m_mod

import logging_conf
logging_conf.testing_setup(__name__)
logger = logging.getLogger('dht')


def assert_almost_equal(result, expected, tolerance=.05):
    if not expected-tolerance < result < expected+tolerance:
        assert False, 'result: %f, expected: %f' % (result,
                                                    expected)

class TestController:

    def setup(self):
        time.mock_mode()
        
        self.controller = controller.Controller(tc.CLIENT_ADDR,
                                                'test_logs/state.dat',
                                                routing_m_mod,
                                                lookup_m_mod,
                                                None)
        self.my_id = self.controller._my_id
        self.querier2 = querier.Querier(self.my_id)

    def _test_start_stop(self):
        self.controller.main_loop()

    def test_simple(self):
        q = querier.Query(message.OutgoingPingQuery(self.my_id),
                          tc.SERVER_NODE)
        expected_ts, expected_msgs = self.querier2.register_queries([q])
        ts, msgs = self.controller.main_loop()
        assert_almost_equal(ts, expected_ts)
        eq_(len(msgs), 1)
        eq_(msgs[0], expected_msgs[0])

    def test_with_unexistent_state_file(self):
        controller.Controller(tc.CLIENT_ADDR, 'test_logs/state.dat.no',
                              routing_m_mod, lookup_m_mod, None)

    def test_adding_and_removing_node(self):
        # The routing table is initially empty
        eq_(self.controller._routing_m.get_main_rnodes(), [])

        q = querier.Query(message.OutgoingPingQuery(self.my_id),
                          tc.SERVER_NODE)
        expected_ts, expected_msgs = self.querier2.register_queries([q])
        # main_loop is called by reactor.start()
        # It returns a maintenance ping
        ts, msgs = self.controller.main_loop()
        assert_almost_equal(ts, expected_ts)
        eq_(len(msgs), 1)
        eq_(msgs[0], expected_msgs[0])
        time.sleep((ts - time.time()) / 2)
        # SERVER_NODE replies before the timeout
        tid = message.IncomingMsg(msgs[0][0], tc.CLIENT_ADDR).tid
        datagram = message.OutgoingPingResponse(tc.SERVER_ID).encode(tid)
        eq_(self.controller._routing_m.get_main_rnodes(), [])
        self.controller.on_datagram_received(datagram, tc.SERVER_ADDR)
        # SERVER_NODE is added to the routing table
        eq_(self.controller._routing_m.get_main_rnodes(), [tc.SERVER_NODE])

        time.sleep((ts - time.time()))
        # main_loop is called to trigger timeout
        # It returns a maintenance lookup
        ts, msgs = self.controller.main_loop() 
        q = querier.Query(message.OutgoingFindNodeQuery(self.my_id,
                                                        self.my_id),
                          tc.SERVER_NODE)
        expected_ts, expected_msgs = self.querier2.register_queries([q])
        assert_almost_equal(ts, expected_ts)
        eq_(len(msgs), 1)
        eq_(msgs[0], expected_msgs[0])
        
        time.sleep(ts - time.time())
        # main_loop is called to trigger timeout
        # It triggers a timeout (removing SERVER_NODE from the routing table
        # returns a maintenance ping
        ts, msgs = self.controller.main_loop()
        eq_(self.controller._routing_m.get_main_rnodes(), [])
        # No reply for this query
        #this call should trigger timeout
        self.controller.main_loop()

    def test_successful_get_peers(self):
        ts, queries = self.controller.main_loop()
        ping_timeout_ts =  ts
        assert_almost_equal(ts, time.time()+2)
        ping, addr = queries[0]
        #fabricate response
        ping = message.IncomingMsg(ping, addr)
        pong = message.OutgoingPingResponse(tc.SERVER_ID)
        data = pong.encode(ping.tid)
        # get a node in the routing table
        self.controller.on_datagram_received(data, addr)
        #The lookup starts with a single node
        lookup_result = []
        ts, msgs = self.controller.get_peers(lookup_result, tc.INFO_HASH,
                                             lambda x,y: x.append(y), 0)
        assert_almost_equal(ts, ping_timeout_ts)#time.time()+2)
        eq_(len(msgs), 1)

        # Now a get_peers with local results
        info_hash = identifier.Id('info_hash info_hash ')
        self.controller._tracker.put(info_hash, tc.CLIENT_ADDR)
        lookup_result = []
        self.controller.get_peers(lookup_result, info_hash,
                                  lambda x,y: x.append(y), 0)
        eq_(len(lookup_result), 1) # the node is tracking this info_hash
        eq_(lookup_result[0][0], tc.CLIENT_ADDR)

    def test_retry_get_peers(self):
        ts, queries = self.controller.main_loop()
        ping_timeout_ts =  ts
        assert_almost_equal(ts, time.time()+2)
        eq_(len(queries), 1)
        ping, addr = queries[0]
        #this get_peers fails because there are no nodes in the routing table
        ts, queries = self.controller.get_peers(None, tc.INFO_HASH, None, 0)
        eq_(len(queries), 0)
        #fabricate response
        ping = message.IncomingMsg(ping, addr)
        pong = message.OutgoingPingResponse(tc.SERVER_ID)
        data = pong.encode(ping.tid)
        # get a node in the routing table
        self.controller.on_datagram_received(data, addr)
        # This call does nothing because it's too early
        ts, queries = self.controller.main_loop()
        #eq_(ts, ping_timeout_ts)
        eq_(queries, [])
        # Controller retries lookup  get_peers
        time.sleep(ts - time.time())
        ts, queries = self.controller.main_loop()
        # The lookup starts with a single node
        ok_(queries)
        assert 'get_peers' in queries[0][0]

    def test_save_state(self):
        time.sleep(controller.SAVE_STATE_DELAY)
        self.controller.main_loop()

    def test_bad_datagram_received(self):
        ts, msgs = self.controller.on_datagram_received('aa', tc.CLIENT_ADDR)
        assert not msgs

    def test_query_received(self):
        #TODO
        pass

    def test_error_received(self):
        #TODO
        pass
        
    def test_complete(self):
        self.controller.print_routing_table_stats()

    def _old(self):
        # controller.start() starts reactor (we don't want to use reactor in
        # tests), sets _running, and calls main_loop
        self.controller._running = True
        # controller.start calls main_loop, which does maintenance (bootstrap)
        self.controller.main_loop()
        # minitwisted informs of a response
        data = message.OutgoingPingResponse(tc.SERVER_ID).encode('\0\0')
        self.controller.on_datagram_received(data, tc.SERVER_ADDR)
        self.controller.main_loop() # maintenance (maintenance lookup)
        
    def teardown(self):
        time.normal_mode()

class TestStateErrors:

    def test(self): 
        '''self.controller = controller.Controller(tc.CLIENT_ADDR,
                                                'test_logs/state.dat.broken',
                                                routing_m_mod,
                                                lookup_m_mod,
                                                None)
'''
        self.controller = controller.Controller(tc.CLIENT_ADDR,
                                                'test_logs/state.dat.good',
                                                routing_m_mod,
                                                lookup_m_mod,
                                                None)
'''
        self.controller = controller.Controller(tc.CLIENT_ADDR,
                                                'test_logs/state.dat.nofile',
                                                routing_m_mod,
                                                lookup_m_mod,
                                                None)
'''
