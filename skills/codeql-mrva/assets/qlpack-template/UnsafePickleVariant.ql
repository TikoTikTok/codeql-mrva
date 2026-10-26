/**
 * @name Unsafe pickle.loads of remote data
 * @description Bytes from a remote source reach pickle.loads without passing through
 *              an authentication check such as hmac.compare_digest.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 9.8
 * @precision medium
 * @id py/variant/unsafe-pickle-remote
 * @tags security
 *       external/cwe/cwe-502
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.TaintTracking
import semmle.python.dataflow.new.RemoteFlowSources
import semmle.python.ApiGraphs

module PickleConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { source instanceof RemoteFlowSource }

  predicate isSink(DataFlow::Node sink) {
    sink = API::moduleImport("pickle").getMember("loads").getACall().getArg(0)
  }

  predicate isBarrier(DataFlow::Node node) {
    // The usual fix: the payload was authenticated first.
    node = API::moduleImport("hmac").getMember("compare_digest").getACall().getArg(_)
  }
}

module PickleFlow = TaintTracking::Global<PickleConfig>;

import PickleFlow::PathGraph

from PickleFlow::PathNode source, PickleFlow::PathNode sink
where PickleFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "pickle.loads of data from $@.", source.getNode(),
  "a remote source"
