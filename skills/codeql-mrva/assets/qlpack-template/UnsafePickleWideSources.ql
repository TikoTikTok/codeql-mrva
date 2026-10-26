/**
 * @name Unsafe pickle.loads of remote data or HTTP client responses
 * @description Bytes from a remote request, or from the response of an outgoing HTTP
 *              request, reach pickle.loads without an authentication check.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 9.8
 * @precision medium
 * @id py/variant/unsafe-pickle-wide-sources
 * @tags security
 *       external/cwe/cwe-502
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.TaintTracking
import semmle.python.dataflow.new.RemoteFlowSources
import semmle.python.Concepts
import semmle.python.ApiGraphs

module PickleConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    source instanceof RemoteFlowSource
    or
    // The value of an outgoing HTTP call (requests.get(...), urllib, httpx, ...) is the
    // response; the framework models carry taint on to .content, .text and .json().
    source instanceof Http::Client::Request
  }

  predicate isSink(DataFlow::Node sink) {
    sink = API::moduleImport("pickle").getMember("loads").getACall().getArg(0)
  }

  predicate isBarrier(DataFlow::Node node) {
    node = API::moduleImport("hmac").getMember("compare_digest").getACall().getArg(_)
  }
}

module PickleFlow = TaintTracking::Global<PickleConfig>;

import PickleFlow::PathGraph

from PickleFlow::PathNode source, PickleFlow::PathNode sink
where PickleFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "pickle.loads of data from $@.", source.getNode(),
  "a remote source or HTTP response"
