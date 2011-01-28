# Copyright (C) 2009-2010 Raul Jimenez
# Released under GNU LGPL 2.1
# See LICENSE.txt for more information

import ptime as time
import logging
import test_const as tc
import routing_table
import message

logger = logging.getLogger('dht')

MAINTENANCE_DELAY = 8

class RoutingManager(object):
    
    def __init__(self, my_node, bootstrap_nodes):
        self.my_node = my_node
        #Copy the bootstrap list
        self.bootstrap_nodes = iter(bootstrap_nodes)

        self.table = routing_table.RoutingTable(my_node, [8,]*160)
        # This is just for testing:
        self.maintenance_counter = 0
        
    def do_maintenance(self):
        self.maintenance_counter += 1
        maintenance_delay = MAINTENANCE_DELAY
        if self.maintenance_counter == 1:
            # bootstrap ping
            queries_to_send = [message.OutgoingPingQuery(tc.SERVER_NODE,
                                                         self.my_node.id)]
            maintenance_lookup_target = None
        elif self.maintenance_counter == 2:
            # maintenance lookup
            queries_to_send = []
            maintenance_lookup_target = self.my_node.id
        else:
            # nothing to do
            queries_to_send = []
            maintenance_lookup_target = None
        return (maintenance_delay, queries_to_send,
                maintenance_lookup_target)
        
    def on_query_received(self, node_):
        '''
        Return None when nothing to do
        Return a list of queries when queries need to be sent (the queries
        will be sent out by the caller)
        '''
        queries_to_send = []
        return queries_to_send
            
    def on_response_received(self, node_, rtt, nodes):
        log_distance = self.my_node.log_distance(node_)
        sbucket = self.table.get_sbucket(log_distance)
        rnode = node_.get_rnode(log_distance)
        rnode.rtt = rtt
        sbucket.main.add(rnode)
        queries_to_send = []
        return queries_to_send
        
    def on_error_received(self, node_): 
        queries_to_send = []
        return queries_to_send
    
    def on_timeout(self, node_):
        log_distance = self.my_node.log_distance(node_)
        sbucket = self.table.get_sbucket(log_distance)
        sbucket.main.remove(node_)
        queries_to_send = []
        return queries_to_send
            
    def get_closest_rnodes(self, log_distance, num_nodes, exclude_myself):
        return self.table.get_closest_rnodes(log_distance,
                                             num_nodes, exclude_myself)

    def get_main_rnodes(self):
        return self.table.get_main_rnodes()

    def print_stats(self):
        pass
