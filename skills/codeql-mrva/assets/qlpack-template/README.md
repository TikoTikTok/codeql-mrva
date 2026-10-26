# Query pack template

Copy this folder, rename the pack in `qlpack.yml` (`<scope>/<name>`, lowercase),
swap `codeql/python-all` for the library of your language, then:

```bash
codeql pack install .                                   # writes codeql-pack.lock.yml; commit it
codeql database analyze <db> PickleLoadsSinks.ql --format=sarif-latest --output=sinks.sarif
codeql database analyze <db> UnsafePickleVariant.ql --format=sarif-latest --output=flow.sarif
```

`PickleLoadsSinks.ql` is the sink-only query (step 1), `UnsafePickleVariant.ql`
the taint-tracking variant (step 2) with `RemoteFlowSource` (incoming web
requests). `UnsafePickleWideSources.ql` adds outgoing HTTP calls
(`Http::Client::Request`) as sources, so a pickle read from a peer's or an
API's response is caught too. All three compile on CodeQL CLI 2.27.3 and carry
the metadata MRVA needs (`@name`, `@id`, `@kind`, `@problem.severity`).
