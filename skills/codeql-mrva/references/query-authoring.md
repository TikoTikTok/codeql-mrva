# Writing the variant query

## Pack layout

```
my-pack/
├── qlpack.yml
├── codeql-pack.lock.yml        # written by `codeql pack install`; commit it
├── PickleLoadsSinks.ql         # step 1: sink-only, @kind problem
└── UnsafePickleVariant.ql      # step 2: taint path, @kind path-problem
```

```yaml
# qlpack.yml
name: yourname/mrva-python-variants     # <scope>/<name>, lowercase
version: 0.0.1
library: false
dependencies:
  codeql/python-all: "*"                # a range the action's CLI can satisfy; never an old exact pin
```

Library pack names: `codeql/actions-all`, `codeql/cpp-all`, `codeql/csharp-all`,
`codeql/go-all`, `codeql/java-all`, `codeql/javascript-all`,
`codeql/python-all`, `codeql/ruby-all`, `codeql/rust-all`, `codeql/swift-all`.
Run `codeql pack install my-pack` after editing dependencies; MRVA bundles
the lock file with the query so the remote compile matches the local one.

## Query metadata

```ql
/**
 * @name Unsafe pickle.loads of request data (variant of CVE-XXXX-YYYY)
 * @description Untrusted bytes reach pickle.loads without a signature check.
 * @kind path-problem            // problem | path-problem
 * @problem.severity error       // error | warning | recommendation
 * @security-severity 9.8        // CVSS-like, drives code scanning severity
 * @precision medium             // low | medium | high | very-high
 * @id py/variant/unsafe-pickle-request    // unique; <lang>/<...>
 * @tags security
 *       external/cwe/cwe-502
 */
```

`@name`, `@id`, `@kind` and `@problem.severity` are required for MRVA to
display the results; `path-problem` queries must `select sink, source, sink,
"message"` and import a `PathGraph`.

## Step 1: sink-only query

Cheap, high recall, shows you the API's shape across the ecosystem.

```ql
/**
 * @name pickle.loads call sites
 * @kind problem
 * @problem.severity warning
 * @id py/variant/pickle-loads-sinks
 */
import python
import semmle.python.ApiGraphs

from API::CallNode call
where call = API::moduleImport("pickle").getMember("loads").getACall()
select call, "pickle.loads with argument $@.", call.getArg(0), call.getArg(0).toString()
```

Run it on the Top 100: the count per repository tells you whether the
pattern is common enough to be worth a taint query and how many results
to expect at full scale.

## Step 2: taint query (modern `ConfigSig` API)

### Python

```ql
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
    // the fix: data that went through hmac.compare_digest / a verify() helper
    node = API::moduleImport("hmac").getMember("compare_digest").getACall().getArg(_)
  }
}

module PickleFlow = TaintTracking::Global<PickleConfig>;
import PickleFlow::PathGraph

from PickleFlow::PathNode source, PickleFlow::PathNode sink
where PickleFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "pickle.loads of data from $@.", source.getNode(), "a remote source"
```

### Java / Kotlin

```ql
import java
import semmle.code.java.dataflow.TaintTracking
import semmle.code.java.dataflow.FlowSources

module Config implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { source instanceof ActiveThreatModelSource }

  predicate isSink(DataFlow::Node sink) {
    exists(MethodCall mc |
      mc.getMethod().hasQualifiedName("java.lang", "Runtime", "exec") and
      sink.asExpr() = mc.getArgument(0)
    )
  }
}

module Flow = TaintTracking::Global<Config>;
import Flow::PathGraph

from Flow::PathNode source, Flow::PathNode sink
where Flow::flowPath(source, sink)
select sink.getNode(), source, sink, "Command built from $@.", source.getNode(), "user input"
```

### JavaScript / TypeScript

```ql
import javascript

module Config implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { source instanceof RemoteFlowSource }

  predicate isSink(DataFlow::Node sink) {
    sink = DataFlow::globalVarRef("eval").getACall().getArgument(0)
  }
}

module Flow = TaintTracking::Global<Config>;
import Flow::PathGraph

from Flow::PathNode source, Flow::PathNode sink
where Flow::flowPath(source, sink)
select sink.getNode(), source, sink, "eval of $@.", source.getNode(), "remote input"
```

### Go

```ql
import go
import semmle.go.security.FlowSources

module Config implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { source instanceof RemoteFlowSource }

  predicate isSink(DataFlow::Node sink) {
    exists(DataFlow::CallNode c |
      c.getTarget().hasQualifiedName("os/exec", "Command") and sink = c.getArgument(0)
    )
  }
}

module Flow = TaintTracking::Global<Config>;
import Flow::PathGraph

from Flow::PathNode source, Flow::PathNode sink
where Flow::flowPath(source, sink)
select sink.getNode(), source, sink, "Command from $@.", source.getNode(), "remote input"
```

### Ruby

```ql
import codeql.ruby.AST
import codeql.ruby.DataFlow
import codeql.ruby.TaintTracking
import codeql.ruby.dataflow.RemoteFlowSources

module Config implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { source instanceof RemoteFlowSource }

  predicate isSink(DataFlow::Node sink) {
    exists(DataFlow::CallNode c | c.getMethodName() = "system" and sink = c.getArgument(0))
  }
}

module Flow = TaintTracking::Global<Config>;
import Flow::PathGraph

from Flow::PathNode source, Flow::PathNode sink
where Flow::flowPath(source, sink)
select sink.getNode(), source, sink, "system() with $@.", source.getNode(), "remote input"
```

### C / C++

```ql
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking
import semmle.code.cpp.security.FlowSources

module Config implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { source instanceof FlowSource }

  predicate isSink(DataFlow::Node sink) {
    exists(FunctionCall fc |
      fc.getTarget().hasGlobalName("system") and sink.asIndirectArgument() = fc.getArgument(0)
    )
  }
}

module Flow = TaintTracking::Global<Config>;
import Flow::PathGraph

from Flow::PathNode source, Flow::PathNode sink
where Flow::flowPath(source, sink)
select sink.getNode(), source, sink, "system() with $@.", source.getNode(), "untrusted input"
```

Library names move between CodeQL releases; `codeql query compile` (or
opening the file in VS Code) is the authority. When a class is missing,
search the standard library: `grep -rn "class RemoteFlowSource" <codeql checkout>/<lang>/ql/lib`.

## Where to get sources and sinks instead of inventing them

- The language's standard library: `RemoteFlowSource`, `ActiveThreatModelSource`,
  `FlowSource`, and the `Concepts` modules (`SystemCommandExecution`,
  `Decoding`, `FileSystemAccess`, `SqlExecution`, `XmlParsing`) that already
  model hundreds of APIs. `sink instanceof SystemCommandExecution` is often
  better than naming one function.
- The shipped security queries (`codeql/<lang>-queries`, `Security/CWE-*`):
  copy their `Config` and swap the sink.
- GitHub Security Lab community packs:
  `githubsecuritylab/codeql-<lang>-queries` and `-libs`
  (https://github.com/GitHubSecurityLab/CodeQL-Community-Packs). Add them
  as a dependency and extend.
- Data extensions (`.model.yml`) for library APIs that are not modelled.

## Precision tuning checklist

Each item is a reason a finding showed up that you did not want. Add the
fix, re-run on the `hits` list, repeat.

| Noise | Fix |
|---|---|
| Test, example, benchmark, vendored code | `not sink.getLocation().getFile().getRelativePath().regexpMatch("(^|.*/)(tests?|spec|examples?|vendor|node_modules|third_party)/.*")` |
| Source is not really attacker-controlled | narrow `isSource` (drop `LocalFlowSource`, env, argv) or use `ActiveThreatModelSource` with the default threat model |
| Data goes through the fix's guard elsewhere | `isBarrier` / `isBarrierIn` for the guard call, or `DataFlow::BarrierGuard` |
| Sink argument is a constant | `not sink.asExpr() instanceof StringLiteral` (per-language equivalent) |
| Same finding repeated per call path | report one per sink: `@kind problem` on the sink with "has flow from a source" message |
| Thousands of results in one repository | that repository is a framework defining the sink; exclude it from the list, not from the query |
| Timeouts on big repositories | avoid `exists` over all nodes without a binding; reduce `isSource` set; use `DataFlow::Global` (value flow) when taint steps are not needed |

`@precision high` only when, on the test databases, the true-positive rate
is above roughly 80 % and the remaining noise is explainable.

## Recall: what `RemoteFlowSource` does not see

`RemoteFlowSource` (Python) models *incoming* requests of web frameworks
(Flask, Django, FastAPI, aiohttp, Tornado, ...). It does not cover:

- responses of *outgoing* HTTP calls (`requests.get(...).content`, urllib, httpx),
- raw sockets, Redis / message-queue payloads, RPC frameworks,
- files, environment, argv (`LocalFlowSource`, usually out of scope anyway).

A zero-result repository is therefore not a clean one for any of those
channels. Widening to outgoing HTTP responses is one line, because the
framework models already carry taint from the response to `.content`,
`.text` and `.json()`:

```ql
import semmle.python.Concepts

predicate isSource(DataFlow::Node source) {
  source instanceof RemoteFlowSource
  or
  source instanceof Http::Client::Request   // the call's value is the response
}
```

`assets/qlpack-template/UnsafePickleWideSources.ql` is the full query. The
`RemoteFlowSource` query misses a `pickle.loads` fed by a peer's HTTP
response; the widened query reports it. Run the narrow
query at scale first (precision), then the wide one on the hits and on the
repositories you care most about (recall). For other channels, model them as
sources with a data extension (`.model.yml`) or a small `DataFlow::Node`
class for the receiving call.

## Unit tests (optional, worth it for a query you will re-run)

```
my-pack-tests/
├── qlpack.yml                 # dependencies: yourname/mrva-python-variants
└── UnsafePickle/
    ├── UnsafePickle.qlref     # "UnsafePickleVariant.ql"
    ├── test.py                # minimal vulnerable + fixed snippets
    └── UnsafePickle.expected  # created by `codeql test run --learn`
```

```bash
codeql test run my-pack-tests --learn   # first time
codeql test run my-pack-tests           # then: fails when results change
```
