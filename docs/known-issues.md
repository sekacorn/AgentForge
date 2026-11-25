# Known issues

Problems that are understood, not yet fixed, and worth knowing about before you
rely on Forge. Each entry says what happens, who it affects, and what to do in
the meantime. Fixed issues move to the [changelog](reference/changelog.md).

## Tool errors carry the tool's own exception text

**Status:** open. Present in 0.5.5 and earlier.

**What happens.** When a tool raises, the sandbox turns the exception into a
tool result and an event:

- the tool result returned to the model is `Error: <exception text>` for a
  `ToolError`, or `Unexpected tool error: <exception text>` for anything else;
- the `tool.call.failed` event carries the same text in its `error` field.

The text is whatever the tool, or a library the tool calls, put in the
exception. Forge does not inspect or shorten it. If that text contains a
secret, such as a URL with a token in it, a connection string or a file path,
the secret reaches the model, the run transcript, and anything that records
events, including audit sinks and tracing.

**Who is affected.** Anyone running tools whose exceptions can contain
sensitive values. That depends on the tool, not on Forge:

- The built-in `http_get` tool is **not** affected from 0.5.5: it reports only
  the type of the exception. The other built-in tools do not handle secrets.
- Tools you write, and tools that wrap HTTP clients, database drivers or cloud
  SDKs, are affected if those libraries put request details in their errors.
  Many do.

**Why it is not simply removed.** The exception text is how a model learns
that it called a tool wrongly and corrects the call. Replacing it with a fixed
message for every tool would make tool use noticeably worse, so the change
needs a design, not a one-line fix. This differs from provider errors, where
the text comes from a remote server and is of no use to the model; those never
repeat upstream text from 0.5.5.

**What to do now.**

- In your own tools, catch exceptions from libraries that handle credentials
  and raise a `ToolError` whose message you wrote yourself. Do not pass the
  library's text through.
- Keep secrets out of tool arguments. A model-supplied URL or query should not
  be where a token lives; have the tool read credentials from configuration.
- Treat `tool.call.failed` events and run transcripts as able to contain
  whatever your tools' errors contain, and protect or scrub them accordingly.

**Where it is.** `forge/security/sandbox.py`, the two `except` branches after
the tool is invoked.

## The pgvector DSN is held as a plain string

**Status:** open. Present in 0.5.5 and earlier.

**What happens.** `ForgeConfig.pgvector_dsn` (`FORGE_PGVECTOR_DSN`) is a
PostgreSQL connection string, which includes the database password. It is
stored as an ordinary `str`. `ollama_base_url` and the API keys are
`SecretStr`, which masks them in `repr` and in serialized output; the DSN is
not masked.

Nothing in Forge prints or logs the configuration, so the password does not
leak in normal operation. The risk is in your own code: `print(config)`,
`config.model_dump()`, a debugger, or an error report that includes the
configuration object would show the DSN in full.

**Who is affected.** Anyone using the pgvector memory backend who logs,
serializes or reports the `ForgeConfig` object.

**What to do now.** Do not log or serialize the whole configuration. If you
must, exclude the field: `config.model_dump(exclude={"pgvector_dsn"})`.

**Planned fix.** Make the field a `SecretStr`, with an accessor so existing
code that reads the DSN keeps working.
