/**
 * @name pickle.loads call sites
 * @description Every call to pickle.loads. Sink-only query: run it first to see how
 *              common the API is before writing the taint-tracking variant.
 * @kind problem
 * @problem.severity warning
 * @precision low
 * @id py/variant/pickle-loads-sinks
 * @tags security
 *       external/cwe/cwe-502
 */

import python
import semmle.python.ApiGraphs

from API::CallNode call
where call = API::moduleImport("pickle").getMember("loads").getACall()
select call, "pickle.loads with argument $@.", call.getArg(0), call.getArg(0).toString()
