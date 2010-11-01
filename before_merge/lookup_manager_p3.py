# Copyright (C) 2009-2010 Raul Jimenez
# Released under GNU LGPL 2.1
# See LICENSE.txt for more information

import sys
import threading
import time

import logging

import querier
import identifier as identifier
import message as message


logger = logging.getLogger('dht')


MAX_PARALLEL_QUERIES = 16

ANNOUNCE_REDUNDANCY = 3

class _QueuedNode(object):

    def __init__(self, node_, log_distance, token):
        self.node = node_
        self.log_distance = log_distance
        self.token = token

    def __cmp__(self, other):
        return self.log_distance - other.log_distance

class _LookupQueue(object):

    def __init__(self, info_hash, queue_size):
        self.info_hash = info_hash
        self.queue_size = queue_size
        self.queue = [_QueuedNode(None, identifier.ID_SIZE_BITS+1, None)]
        # *_ips is used to prevent that many Ids are
        # claimed from a single IP address.
        self.queued_ips = set()
        self.queried_ips = set()
        self.queued_qnodes = []
        self.responded_qnodes = []
        self.max_queued_qnodes = 8
        self.max_responded_qnodes = 16

        self.last_query_ts = time.time()
        self.slow_down = False
        self.pop_counter = 0

    def bootstrap(self, nodes):
        # Assume that the ips are not duplicated.
        for n in nodes:
            self.queried_ips.add(n.ip)
        return nodes

    def on_response(self, src_node, nodes, token):
        ''' Nodes must not be duplicated'''
        qnode = _QueuedNode(src_node,
                            src_node.id.log_distance(self.info_hash),
                            token)
        self._add_responded_qnode(qnode)
        qnodes = [_QueuedNode(n, n.id.log_distance(
                    self.info_hash), token)
                  for n in nodes]
        self._add_queued_qnodes(qnodes)
        return self._pop_nodes_to_query()

    def on_timeout(self, src_node):
        return self._pop_nodes_to_query()

    def get_closest_qnodes(self, num_nodes=ANNOUNCE_REDUNDANCY):
        result = []
        for qnode in self.responded_qnodes:
            if qnode.token:
                result.append(qnode)
                if len(result) == num_nodes:
                    break
        return result
    
    def _add_queried_ip(self, ip):
        if ip not in self.queried_ips:
            self.queried_ips.add(ip)
            return True
        
    def _add_responded_qnode(self, qnode):
        self.responded_qnodes.append(qnode)
        self.responded_qnodes.sort()
        del self.responded_qnodes[self.max_responded_qnodes:]

    def _add_queued_qnodes(self, qnodes):
        for qnode in qnodes:
#            print 'adding qnode', qnode
            if qnode.node.ip not in self.queued_ips \
                    and qnode.node.ip not in self.queried_ips:
                self.queued_qnodes.append(qnode)
                self.queued_ips.add(qnode.node.ip)
        self.queued_qnodes.sort()
        for qnode  in self.queued_qnodes[self.max_queued_qnodes:]:
            self.queued_ips.remove(qnode.node.ip)
        del self.queued_qnodes[self.max_queued_qnodes:]

    def _pop_nodes_to_query(self):
        self.pop_counter += 1
        nodes_to_query = []
        if not self.slow_down and self.pop_counter % 2:
            marks_index = (3, 0,)
        else:
            marks_index = (3,)
        marks = []
        for i in marks_index:
            if len(self.responded_qnodes) > i:
                marks.append(self.responded_qnodes[i].log_distance)
            else:
                marks.append(identifier.ID_SIZE_BITS)
        for mark in marks:
            try:
                qnode = self.queued_qnodes[0]
            except (IndexError):
                break # no more queued nodes left
            if qnode.log_distance < mark:
                self.queried_ips.add(qnode.node.ip)
                nodes_to_query.append(qnode.node)
                del self.queued_qnodes[0]
                self.queued_ips.remove(qnode.node.ip)
        self.last_query_ts = time.time()
        return nodes_to_query

   
class GetPeersLookup(object):
    """DO NOT use underscored variables, they are thread-unsafe.
    Variables without leading underscore are thread-safe.

    All nodes in bootstrap_nodes MUST have ID.
    """

    def __init__(self, my_id,
                 info_hash, callback_f, bootstrap_nodes,
                 bt_port=None):
        logger.debug('New lookup (info_hash: %r)' % info_hash)
        self._my_id = my_id
        self._get_peers_msg = message.OutgoingGetPeersQuery(
            my_id, info_hash)
        self._callback_f = callback_f
        self._lookup_queue = _LookupQueue(info_hash, 20)
        self.bootstrap_nodes = bootstrap_nodes
                                     
        self._info_hash = info_hash
        self._bt_port = bt_port
        self._lock = threading.RLock()

        self._num_parallel_queries = 0
        self._is_done = False

        self.num_queries = 0
        self.num_responses = 0
        self.num_timeouts = 0
        self.num_errors = 0

        self._running = False

    @property
    def is_done(self):
        #with self._lock:
        self._lock.acquire()
        try:
            is_done = self._is_done
        finally:
            self._lock.release()
        return is_done

    def get_num_parallel_queries(self):
        #with self._lock:
        self._lock.acquire()
        try:
            n = self._num_parallel_queries
        finally:
            self._lock.release()
        return n
    def set_num_parallel_queries(self, n):
        self._lock.acquire()
        try:
            self._num_parallel_queries = n
        finally:
            self._lock.release()
    num_parallel_queries = property(get_num_parallel_queries,
                                    set_num_parallel_queries)
            
    def start(self):
        assert not self._running
        self._running = True
        nodes_to_query = self._lookup_queue.bootstrap(self.bootstrap_nodes)
        result = self._get_queries(nodes_to_query)
#        print 'starting lookup...'
        return result
        
    def on_response_received(self, response_msg, node_):
        self.num_parallel_queries -= 1
        self.num_responses += 1
        '''print '[%.4f] response %d from (%d-%d)' % (
            time.time(),
            self.num_responses,
            self._my_id.log_distance(node_.id),
            self._info_hash.log_distance(node_.id))
        '''
        logger.debug('response from %r\n%r' % (node_,
                                                response_msg))
        token = getattr(response_msg, 'token', None)
        peers = getattr(response_msg, 'peers', None)
        if peers:
            self._lookup_queue.slow_down = True
            self._callback_f(peers)
#        print 'all_nodes', response_msg.all_nodes
        nodes_to_query = self._lookup_queue.on_response(node_,
                                                        response_msg.all_nodes,
                                                        token)
#        print 'nodes_to_query', nodes_to_query
        #FIXME: all_nodes instead of nodes >> fixed???
        result = self._get_queries(nodes_to_query)
        self._end_lookup_when_done()
        return result

    def on_timeout(self, node_):
        self._lookup_queue.slow_down = True
        nodes_to_query = self._lookup_queue.on_timeout(node_) 
        result = self._get_queries(nodes_to_query)
                                                       
        self.num_timeouts += 1
        #print '[%.4f] timeout %d' % (time.time(), self.num_timeouts)
        logger.debug('TIMEOUT node: %r' % node_)
        self.num_parallel_queries -= 1
        self._end_lookup_when_done()
        return result
    
    def on_error_received(self, error_msg, node_):
        self.num_errors += 1
        #print '[%.4f] error %d' % (time.time(), self.num_errors)
        logger.debug('ERROR node: %r' % node_)
        self.num_parallel_queries -= 1
        self._end_lookup_when_done()
        return []

    def _end_lookup_when_done(self):
        if self.num_parallel_queries == 0:
            # This is the last pending query
            # TODO: callback end_of_lookup()
            print '[%.4f] end of lookup:' % time.time()
            print 'response ratio %d/%d (responses/timeouts)' % (
                self.num_responses,
                self.num_timeouts)
            self._announce()
        
    def _get_queries(self, nodes):
        result = []
        for node_ in nodes:
            if node_.id == self._my_id:
                # Don't send to myself
                continue
            self.num_parallel_queries += 1
            self.num_queries += len(nodes)
            result.append(querier.Query(self._get_peers_msg, node_, self))
        return result

    def _do_nothing(self, *args, **kwargs):
        #TODO2: generate logs
        pass

    def _announce(self):
        self._is_done = True
        if not self._bt_port:
            return
        result = []
        for qnode in self._lookup_queue.get_closest_qnodes():
            logger.debug('announcing to %r' % qnode.node)
            msg = message.OutgoingAnnouncePeerQuery(
                self._my_id, self._info_hash, self._bt_port, qnode.token)
            result.append((msg, qnode.node))
        return result

            
class BootstrapLookup(GetPeersLookup):

    def __init__(self, my_id, querier, max_parallel_queries, target, nodes):
        GetPeersLookup.__init__(self, my_id, querier, max_parallel_queries,
                                target, None, nodes)
        self._get_peers_msg = message.OutgoingFindNodeQuery(my_id,
                                                            target)
            
        
class LookupManager(object):

    def __init__(self, my_id, querier_, routing_m,
                 max_parallel_queries=MAX_PARALLEL_QUERIES):
        self.my_id = my_id
        self.querier = querier_
        self.routing_m = routing_m
        self.max_parallel_queries = max_parallel_queries


    def get_peers(self, info_hash, callback_f, bt_port=None):
        lookup_q = GetPeersLookup(
            self.my_id, info_hash, callback_f,
            self.routing_m.get_closest_rnodes(info_hash),
            bt_port)
#        lookup_q.start()
        return lookup_q

    def bootstrap_lookup(self, target=None):
        target = target or self.my_id
        lookup_q = BootstrapLookup(
            self.my_id, self.querier,
            self.max_parallel_queries,
            target,
            self.routing_m.get_closest_rnodes(target))
#        lookup_q.start()
        return lookup_q

    def stop(self):
        self.querier.stop()


#TODO2: During the lookup, routing_m gets nodes_found and sends find_node
        # to them (in addition to the get_peers sent by lookup_m)
