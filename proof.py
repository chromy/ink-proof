#!/usr/bin/env python3
import sys
import time
import re
import os
import asyncio
import tempfile
import contextlib
import json
import shutil
import argparse
import itertools

SEM = None

DEFAULT_OUT_PATH = os.path.abspath("out")
DEFAULT_TIMEOUT_S = 20
DEFAULT_COMPILER = "inklecate_v0.9.0"
DEFAULT_RUNTIME = "inklecate_runtime_v0.9.0+"

def serve(directory, port):
  import http.server
  import socketserver
  class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
      super().__init__(*args, directory=directory, **kwargs)
  with socketserver.TCPServer(("", port), Handler) as httpd:
    print(f"Serving test output at http://localhost:{port}",)
    httpd.serve_forever()

class SummaryItem(object):
    def __init__(self, name, human_name):
        self.name = name
        self.human_name = human_name

    def describe(self):
        return {
            "name": self.name,
            "humanName": self.human_name,
        }

class Status(object):
    def __init__(self, name, symbol, description, summary=None):
        self.name = name
        self.symbol = symbol
        self.description = description
        self.summary = summary if summary else []

    def describe(self):
        return {"name": self.name,
                "description": self.description,
                "symbol": self.symbol,
                "summary": [s.describe() for s in self.summary],
                }

SuccessStatus = Status(
    "SUCCESS",
    "💚",
    "",
    [
        SummaryItem("outPath", "Output"),
    ]
)

FailStatus = Status("FAIL", "❌", "Actual output does not match expected", [
    SummaryItem("outPath", "Actual output"),
    SummaryItem("expectedPath", "Expected output"),
    SummaryItem("diffPath", "Diff"),
])

ErrorCompilerDidNotOutputStatus = Status("COMPILER_NO_OUTPUT", "❌", "The compiler did not produce output", [
    SummaryItem("compileExitcode", "Exit code"),
    SummaryItem("compileOutPath", "stdout"),
    SummaryItem("compileErrPath", "stderr"),
])
ErrorRuntimeCrashedStatus = Status("RUNTIME_CRASHED", "🔥", "The runtime crashed on this input", [
    SummaryItem("exitcode", "Exit code"),
    SummaryItem("outPath", "stdout"),
    SummaryItem("errPath", "stderr"),
])
ErrorCompilerCrashedStatus = Status("COMPILER_CRASHED", "🔥", "The compiler crashed on this input", [
    SummaryItem("compileExitcode", "Exit code"),
    SummaryItem("compileOutPath", "stdout"),
    SummaryItem("compileErrPath", "stderr"),
])

TimeoutStatus = Status("RUNTIME_TIMEOUT", "⌛", "The runtime timed out", [
    SummaryItem("exitcode", "Exit code"),
    SummaryItem("outPath", "stdout"),
    SummaryItem("errPath", "stderr")
])

InfraErrorStatus = Status("INFRA_ERROR", "🏗️", "Infra error", [
    SummaryItem("infraError", "Exception"),
])

class PlayerResult(object):
    def __init__(self, program, example, player_job, diff_job, compile_job=None, softwear_under_test):
        self.program = program
        self.example = example
        self.player_job = player_job
        self.diff_job = diff_job
        self.infra_error = None
        self.compile_job = compile_job
        self.softwear_under_test = self.program

    def settle(self):
        if self.player_job.timed_out:
            self.status = TimeoutStatus
        elif self.player_job.infra_error:
            self.status = InfraErrorStatus
            self.infra_error = self.player_job.infra_error
        elif self.player_job.return_code != 0:
            self.status = ErrorRuntimeCrashedStatus
        elif self.diff_job.return_code == 1:
            # inklecate has 0 exit code on exception and emits BOM
            if os.path.getsize(self.player_job.stderr_path) > 5:
                self.status = ErrorRuntimeCrashedStatus
            else:
                self.status = FailStatus
        else:
            self.status = SuccessStatus

    def describe(self):
        diff_path = os.path.relpath(self.diff_job.stdout_path, 'out')
        out_path = os.path.relpath(self.player_job.stdout_path, 'out')
        err_path = os.path.relpath(self.player_job.stderr_path, 'out')
        # TODO(chromy): Clean up
        root = os.path.dirname(os.path.abspath(__file__))
        transcript_path = os.path.relpath(self.example.transcript_path, root)
        description = {
            "status": self.status.name,
            "program": self.program.name,
            "example": self.example.name,
            "diffPath": diff_path,
            "outPath": out_path,
            "errPath": err_path,
            "expectedPath": transcript_path,
            "exitcode": self.player_job.return_code,
            "playCmdline": self.player_job.nice_command(),
        }
        if self.compile_job:
            compile_stdout_path = os.path.relpath(self.compile_job.stdout_path, 'out')
            compile_stderr_path = os.path.relpath(self.compile_job.stderr_path, 'out')
            compile_bytecode_path = os.path.relpath(self.compile_job.out_path, 'out')
            description["compileCmdline"] = self.compile_job.command
            description["compileOutPath"] = compile_stdout_path
            description["compileErrPath"] = compile_stderr_path
            description["compileBytecodePath"] = compile_bytecode_path
            description["compileExitcode"] = self.compile_job.return_code

        if self.infra_error:
            description["infraError"] = str(self.infra_error)
        return description

class CompilerResult(object):
    def __init__(self, compiler, runtime, example, compile_job, player_job, diff_job):
        self.compiler = compiler
        self.runtime = runtime
        self.example = example
        self.compile_job = compile_job
        self.player_job = player_job
        self.diff_job = diff_job
        self.infra_error = None
        self.softwear_under_test = self.compiler

    def settle(self):
        if self.compile_job.timed_out:
            self.status = TimeoutStatus
        elif self.compile_job.infra_error:
            self.status = InfraErrorStatus
            self.infra_error = self.compile_job.infra_error
        elif self.compile_job.return_code:
            self.status = ErrorCompilerCrashedStatus
        elif not os.path.isfile(self.compile_job.out_path):
            self.status = ErrorCompilerDidNotOutputStatus
        elif self.player_job.timed_out:
            self.status = TimeoutStatus
        elif self.player_job.infra_error:
            self.status = InfraErrorStatus
            self.infra_error = self.player_job.infra_error
        elif self.player_job.return_code != 0:
            self.status = ErrorRuntimeCrashedStatus
        elif self.diff_job.return_code == 1:
            # inklecate has 0 exit code on exception and emits BOM
            if os.path.getsize(self.player_job.stderr_path) > 5:
                self.status = ErrorRuntimeCrashedStatus
            else:
                self.status = FailStatus
        else:
            self.status = SuccessStatus

    def describe(self):
        diff_path = os.path.relpath(self.diff_job.stdout_path, 'out')
        out_path = os.path.relpath(self.player_job.stdout_path, 'out')
        err_path = os.path.relpath(self.player_job.stderr_path, 'out')

        compile_stdout_path = os.path.relpath(self.compile_job.stdout_path, 'out')
        compile_stderr_path = os.path.relpath(self.compile_job.stderr_path, 'out')
        compile_bytecode_path = os.path.relpath(self.compile_job.out_path, 'out')

        # TODO(chromy): Clean up
        root = os.path.dirname(os.path.abspath(__file__))
        transcript_path = os.path.relpath(self.example.transcript_path, root)

        description = {
            "status": self.status.name,
            "program": self.compiler.name,
            "compiler": self.compiler.name,
            "runtime": self.runtime.name,
            "example": self.example.name,
            "diffPath": diff_path,
            "outPath": out_path,
            "errPath": err_path,
            "expectedPath": transcript_path,
            "compileCmdline": self.compile_job.nice_command(),
            "compileOutPath": compile_stdout_path,
            "compileErrPath": compile_stderr_path,
            "compileBytecodePath": compile_bytecode_path,
            "compileExitcode": self.compile_job.return_code,
            "diffExitcode": self.diff_job.return_code,
            "exitcode": self.player_job.return_code,
        }
        if self.infra_error:
            description["infraError"] = str(self.infra_error)
        return description

def check_path(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

class BytecodeExample(object):
    def __init__(self, name, bytecode_path, input_path, transcript_path, metadata_path):
        self.name = name
        self.bytecode_path = bytecode_path
        self.input_path = input_path
        self.transcript_path = transcript_path
        self.metadata_path = metadata_path
        self._metadata = None

    def __lt__(self, o):
        return self.name < o.name

    def check(self):
        check_path(self.bytecode_path)

    def metadata(self):
        if not self._metadata:
            with open(self.metadata_path) as f:
                self._metadata = json.load(f)
        return self._metadata

    def should_ignore(self):
        return self.metadata().get("hide", False)

    def describe(self):
        source_path = os.path.relpath(self.bytecode_path)
        input_path = os.path.relpath(self.input_path)
        expected_path = os.path.relpath(self.transcript_path)
        return {
            "name": self.name,
            "sourcePath": source_path,
            "inputPath": input_path,
            "expectedPath": expected_path,
            "metadata": self.metadata(),
        }

    @staticmethod
    def fromDirAndName(root, name):
        bytecode_path = os.path.join(root, name, 'bytecode.json')
        input_path = os.path.join(root, name, 'input.txt')
        transcript_path = os.path.join(root, name, 'transcript.txt')
        metadata_path = os.path.join(root, name, 'metadata.json')
        return BytecodeExample(name, bytecode_path, input_path, transcript_path, metadata_path)

class InkExample(object):
    def __init__(self, name, ink_path, input_path, transcript_path, metadata_path):
        self.name = name
        self.ink_path = ink_path
        self.input_path = input_path
        self.transcript_path = transcript_path
        self.metadata_path = metadata_path
        self._metadata = None

    def __lt__(self, o):
        return self.name < o.name

    def metadata(self):
        if not self._metadata:
            with open(self.metadata_path) as f:
                self._metadata = json.load(f)
        return self._metadata

    def should_ignore(self):
        return self.metadata().get("hide", False)

    def describe(self):
        source_path = os.path.relpath(self.ink_path)
        input_path = os.path.relpath(self.input_path)
        expected_path = os.path.relpath(self.transcript_path)
        return {
            "name": self.name,
            "sourcePath": source_path,
            "inputPath": input_path,
            "expectedPath": expected_path,
            "metadata": self.metadata(),
        }

    def check(self):
        check_path(self.transcript_path)

    @staticmethod
    def fromDirAndName(root, name):
        ink_path = os.path.join(root, name, 'story.ink')
        input_path = os.path.join(root, name, 'input.txt')
        transcript_path = os.path.join(root, name, 'transcript.txt')
        metadata_path = os.path.join(root, name, 'metadata.json')
        return InkExample(name, ink_path, input_path, transcript_path, metadata_path)



class PlayerDriver(object):
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.human_name = name.replace('_', ' ')

    def __lt__(self, o):
        return self.name < o.name

    def describe(self):
        return {
            "name": self.name,
            "kind": "Runtime",
        }

class CompilerDriver(object):
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.human_name = name.replace('_', ' ')

    def __lt__(self, o):
        return self.name < o.name

    def describe(self):
        return {
            "name": self.name,
            "kind": "Compiler",
        }

def find_all_bytecode_examples(root):
    folder = os.path.join(root, 'bytecode')
    files = os.listdir(folder)
    names = set([name for name in files if os.path.isdir(os.path.join(folder, name))])
    examples = [BytecodeExample.fromDirAndName(folder, name) for name in names]
    return sorted(examples)

def find_all_ink_examples(root):
    folder = os.path.join(root, 'ink')
    files = os.listdir(folder)
    names = set([name for name in files if os.path.isdir(os.path.join(folder, name))])
    examples = [InkExample.fromDirAndName(folder, name) for name in names]
    return sorted(examples)

def find_all_player_drivers(root):
    folder = os.path.join(root, 'drivers')
    files = os.listdir(folder)
    suffix = "_runtime_driver"
    names = [name for name in files if name.endswith(suffix)]
    drivers = [PlayerDriver(name[:-len(suffix)], os.path.join(root, "drivers", name)) for name in names]
    return sorted(drivers)

def find_all_complier_drivers(root):
    folder = os.path.join(root, 'drivers')
    files = os.listdir(folder)
    suffix = "_compiler_driver"
    names = [name for name in files if name.endswith(suffix)]
    drivers = [CompilerDriver(name[:-len(suffix)], os.path.join(root, "drivers", name)) for name in names]
    return sorted(drivers)


class Job(object):
    def __init__(self, command, stdout_path=None, stderr_path=None, stdin_path=None, deps=None, timeout=DEFAULT_TIMEOUT_S, expected_paths=None):
        self.command = command
        self.stdin_path = stdin_path
        self.stderr_path = stderr_path
        self.stdout_path = stdout_path
        self.task = None
        self.deps = deps if deps else []
        self.return_code = None
        self.timed_out = False
        self.infra_error = None
        self.timeout = timeout
        self.expected_paths = expected_paths if expected_paths else []
        if self.stdin_path:
            self.expected_paths.append(self.stdin_path)

    def begin(self):
        self.task = asyncio.create_task(self.run())

    def nice_command(self):
        if self.stdin_path:
            return ["cat", self.stdin_path, "|"] + self.command
        return self.command

    async def run(self):
        if self.deps:
            done, pending = await asyncio.wait([dep.task for dep in self.deps])
        for dep in self.deps:
            if dep.return_code != 0:
                return
        await SEM.acquire()
        try:
            await self.do_work()
        finally:
            SEM.release()

    async def do_work(self):
        for path in self.expected_paths:
            if not os.path.isfile(path):
                self.infra_error = FileNotFoundError(path)
                return
        fin = open(self.stdin_path) if self.stdin_path else None
        fout = open(self.stdout_path, 'wb') if self.stdout_path else None
        ferr = open(self.stderr_path, 'wb') if self.stderr_path else None
        # print('Running "{}"'.format(' '.join(self.command)))
        try:
            process = await asyncio.create_subprocess_exec(self.command[0], *self.command[1:], stdout=fout, stderr=ferr, stdin=fin)
        except PermissionError as e:
            self.infra_error = e
        except FileNotFoundError as e:
            self.infra_error = e
        else:
            try:
                self.return_code = await asyncio.wait_for(process.wait(), self.timeout)
            except asyncio.TimeoutError as e:
                self.timed_out = True
                process.terminate()
                self.return_code = await asyncio.wait_for(process.wait(), self.timeout)
        if fout:
            fout.close()
        if ferr:
            ferr.close()
        if fin:
            fin.close()

def make_name(*things, suffix=None):
    return '_'.join([thing.name for thing in things]) + suffix

def player_job(player, bytecode, output_directory, timeout, deps=None):
    stderr_path = os.path.join(output_directory, make_name(player, bytecode, suffix='_stderr.txt'))
    stdout_path = os.path.join(output_directory, make_name(player, bytecode, suffix='_stdout.txt'))
    return Job([sys.executable, player.path, bytecode.bytecode_path], stderr_path=stderr_path, stdout_path=stdout_path, stdin_path=bytecode.input_path, timeout=timeout, deps=deps)

def player2_job(player, bytecode, bytecode_path, input_path, output_directory, timeout, deps=None):
    stderr_path = os.path.join(output_directory, make_name(player, bytecode, suffix='_stderr.txt'))
    stdout_path = os.path.join(output_directory, make_name(player, bytecode, suffix='_stdout.txt'))
    return Job([sys.executable, player.path, bytecode_path], stderr_path=stderr_path, stdout_path=stdout_path, stdin_path=input_path, timeout=timeout, deps=deps)

def compile_player_job(compiler, player, example, bytecode_path, output_directory, timeout, deps=None):
    stderr_path = os.path.join(output_directory, make_name(compiler, player, example, suffix='_stderr.txt'))
    stdout_path = os.path.join(output_directory, make_name(compiler, player, example, suffix='_stdout.txt'))
    return Job([sys.executable, player.path, bytecode_path], stderr_path=stderr_path, stdout_path=stdout_path, stdin_path=example.input_path, timeout=timeout, deps=deps, expected_paths=[bytecode_path])

def compile_job(compiler, ink, output_directory, timeout):
    stderr_path = os.path.join(output_directory, make_name(compiler, ink, suffix='_stderr.txt'))
    stdout_path = os.path.join(output_directory, make_name(compiler, ink, suffix='_stdout.txt'))
    out_path = os.path.join(output_directory, make_name(compiler, ink, suffix='_out.json'))
    job = Job([sys.executable, compiler.path, "-o", out_path, ink.ink_path], stderr_path=stderr_path, stdout_path=stdout_path, timeout=timeout)
    job.out_path = out_path
    return job

def diff_job(a_path, b_path, out_path, deps=None):
    return Job([sys.executable, 'diff.py', a_path, b_path], stdout_path=out_path, deps=deps)

def job_stats(jobs):
    total = 0
    done = 0
    for job in jobs:
        total += 1
        if job.task and job.task.done():
            done += 1
    return done, total

async def run_jobs(jobs, results):
    global SEM
    SEM = asyncio.Semaphore(30)
    for job in jobs:
        job.begin()
    # print(job_stats(jobs))
    done, pending = await asyncio.wait([job.task for job in jobs])
    # print(job_stats(jobs))
    # print(done)

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)
    return directory

def write_json(fout, drivers, examples, results):
    metadata = {
        "timestamp": time.time()
    }
    statuses = {status.name: status.describe() for status in [
        SuccessStatus,
        FailStatus,
        ErrorCompilerDidNotOutputStatus,
        ErrorRuntimeCrashedStatus,
        ErrorCompilerCrashedStatus,
        TimeoutStatus,
        InfraErrorStatus,
    ]}
    drivers = [driver.describe() for driver in drivers]
    examples = [example.describe() for example in examples]
    results = [result.describe() for result in results]

    json.dump({
        "metadata": metadata,
        "statuses": statuses,
        "programs": drivers,
        "examples": examples,
        "results": results,
    }, fout)

def decide_exit_status(results):
    any_failed = any(r.status is FailStatus for r in results)
    any_non_success = any(r.status is not SuccessStatus for r in results)
    if any_failed:
        return 1
    elif any_non_success:
        return 2
    else:
        return 0

def summarise_results(results):
    total = len(results)
    passed = len([r for r in results if r.status is SuccessStatus])
    return f'{passed}/{total} passed'

def render_badge(label, message, color):
    caption = f'{label}: {message}'
    label_size = 6 * len(label)
    message_size = 6 * len(message)
    margin = 10
    label_width = label_size + margin * 2
    message_width = message_size + margin * 2
    total_width = label_width + message_width
    label_x = margin + label_size / 2
    message_x = label_size + 3 * margin + message_size / 2
    return f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{total_width}" height="20" role="img" aria-label="{caption}">
  <title>{caption}</title>
  <g shape-rendering="crispEdges">
  <rect width="{label_width}" height="20" fill="#555"/>
  <rect x="{label_width}" width="{message_width}" height="20" fill="{color}"/></g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="110">
  <text x="{label_x*10}" y="140" transform="scale(.1)" fill="#fff" textLength="{label_size*10}">{label}</text>
  <text x="{message_x*10}" y="140" transform="scale(.1)" fill="#fff" textLength="{message_size*10}">{message}</text>
 </g></svg>'''

def write_badges(results, output_directory):
    keyfunc = lambda r: r.softwear_under_test
    results = sorted(results, key=keyfunc)
    for softwear_under_test, rs in itertools.groupby(results, key=keyfunc):
        rs = list(rs)
        label = softwear_under_test.human_name
        name = softwear_under_test.name
        badge_path = os.path.join(output_directory, f'{name}.svg')
        total = len(rs)
        passed = len([r for r in rs if r.status is SuccessStatus])
        color = '#97ca00' if total == passed else '#e05d44'
        with open(badge_path, 'w') as f:
            f.write(render_badge(label, f'{passed}/{total}', color))

def main(root):
    bytecode_examples = find_all_bytecode_examples(root)
    ink_examples = find_all_ink_examples(root)
    compiler_drivers = find_all_complier_drivers(root)
    player_drivers = find_all_player_drivers(root)

    available_runtimes = [d.name for d in player_drivers]
    available_compilers = [d.name for d in compiler_drivers]
    available_drivers = available_runtimes + available_compilers
    default_drivers = [name for name in available_drivers if "test" not in name]
    drivers_by_name = {d.name: d for d in compiler_drivers + player_drivers}

    parser = argparse.ArgumentParser(description='Testing for Ink compilers and runtimes')
    parser.add_argument('--out', default=DEFAULT_OUT_PATH, help=f'output directory (default: {DEFAULT_OUT_PATH})')
    parser.add_argument('--list-drivers', action='store_true', help='list found compilers and runtimes')
    parser.add_argument('--timeout', default=DEFAULT_TIMEOUT_S, type=int, help=f'timeout for subprocesses (default: {DEFAULT_TIMEOUT_S}s)')
    parser.add_argument('--reference-runtime', default=DEFAULT_RUNTIME, help=f'set the reference runtime (default: {DEFAULT_RUNTIME})')
    parser.add_argument('--reference-compiler', default=DEFAULT_COMPILER, help=f'set the reference compiler (default: {DEFAULT_COMPILER})')
    parser.add_argument('--examples', default=".*", help=f'filter for examples (default: .*)')
    parser.add_argument('--serve', nargs='?', const=8000, type=int, help=f'after running serve the output directory at given port (default: 8000)')
    parser.add_argument('drivers', nargs='*', default=default_drivers, help=f'drivers to test (default: {" ".join(default_drivers)}) (available: {" ".join(available_runtimes+available_compilers)})')
    args = parser.parse_args()

    selected_drivers = []
    if args.reference_runtime not in available_runtimes:
        runtimes = ", ".join(available_runtimes)
        parser.error(f"Runtime '{args.reference_runtime}' unknown. Available runtimes: {runtimes}")
    if args.reference_compiler not in available_compilers:
        compilers = ", ".join(available_compilers)
        parser.error(f"Compiler '{args.reference_compiler}' unknown. Available compilers: {compilers}")
    for name in args.drivers:
        if name not in available_runtimes and name not in available_compilers:
            drivers = ", ".join(available_drivers)
            parser.error(f"Driver '{name}' unknown. Available drivers: {drivers}")
        selected_drivers.append(drivers_by_name[name])
    if len(args.drivers) != len(set(args.drivers)):
        parser.error(f"Drivers \"{' '.join(args.drivers)}\" contains duplicates")

    reference_runtime, = [d for d in player_drivers if d.name == args.reference_runtime]
    reference_compiler, = [d for d in compiler_drivers if d.name == args.reference_compiler]

    if args.list_drivers:
        print("Available runtimes:")
        for d in player_drivers:
            suffix = " (reference runtime)" if d == reference_runtime else ""
            print(f"\t{d.name}{suffix}")
        print("Available compilers:")
        for d in compiler_drivers:
            suffix = " (reference compiler)" if d == reference_compiler else ""
            print(f"\t{d.name}{suffix}")
        return 0

    selected_compilers = []
    selected_runtimes = []
    for d in compiler_drivers:
        if d.name in args.drivers:
            selected_compilers.append(d)
    for d in player_drivers:
        if d.name in args.drivers:
            selected_runtimes.append(d)

    r = re.compile(args.examples)
    bytecode_examples = [e for e in bytecode_examples if r.match(e.name)]
    ink_examples = [e for e in ink_examples if r.match(e.name)]
    bytecode_examples = [e for e in bytecode_examples if not e.should_ignore()]
    ink_examples = [e for e in ink_examples if not e.should_ignore()]

    if not bytecode_examples and not ink_examples:
        parser.error(f"The example regex \"{args.examples}\" matches no examples.")

    if ink_examples and not selected_compilers:
        compilers = ", ".join(available_compilers)
        parser.error(f"You must nominate a compiler driver to run Ink test cases. Available compilers: {compilers}")

    try:
        for example in bytecode_examples + ink_examples:
            example.check()
    except FileNotFoundError as e:
        print(f"Example {example.name} invalid. Missing file '{e}'", file=sys.stderr)
        exit(1)

    with contextlib.ExitStack() as context_stack:
        # output_directory = context_stack.enter_context(tempfile.TemporaryDirectory())
        output_directory = ensure_dir('out')


        jobs = []
        results = []

        refrence_compile_job = {}
        for j, example in enumerate(ink_examples):
            for i, compiler in enumerate(selected_compilers):
                job_a = compile_job(compiler, example, output_directory, args.timeout)
                job_b = compile_player_job(compiler, reference_runtime, example, job_a.out_path, output_directory, args.timeout, deps=[job_a])
                diff_path = os.path.join(output_directory, make_name(compiler, reference_runtime, example, suffix='_diff.txt'))
                job_c = diff_job(example.transcript_path, job_b.stdout_path, diff_path, deps=[job_b])
                jobs.extend([job_a, job_b, job_c])

                results.append(CompilerResult(compiler, reference_runtime, example, job_a, job_b, job_c))
                if compiler == reference_compiler:
                    refrence_compile_job[example] = job_a

            for i, runtime in enumerate(selected_runtimes):
                job_z = refrence_compile_job[example]
                bytecode_path = job_z.out_path
                input_path = example.input_path
                job_a = player2_job(runtime, example, bytecode_path, input_path, output_directory, args.timeout, deps=[job_z])
                diff_path = os.path.join(output_directory, make_name(runtime, example, suffix='_diff.txt'))
                job_b = diff_job(example.transcript_path, job_a.stdout_path, diff_path, deps=[job_a])
                jobs.append(job_a)
                jobs.append(job_b)
                results.append(PlayerResult(runtime, example, job_a, job_b, compile_job=job_z))

        for j, example in enumerate(bytecode_examples):
            for i, player in enumerate(selected_runtimes):
                job_a = player_job(player, example, output_directory, args.timeout)
                diff_path = os.path.join(output_directory, make_name(player, example, suffix='_diff.txt'))
                job_b = diff_job(example.transcript_path, job_a.stdout_path, diff_path, deps=[job_a])
                jobs.append(job_a)
                jobs.append(job_b)
                results.append(PlayerResult(player, example, job_a, job_b))
        asyncio.run(run_jobs(jobs, results))

        shutil.copyfile(os.path.join(root, 'index.html'), os.path.join(output_directory, 'index.html'))
        shutil.copyfile(os.path.join(root, 'docs', 'logo.png'), os.path.join(output_directory, 'favicon.png'))

        output_bytecode_path = os.path.join(output_directory, 'bytecode')
        if os.path.exists(output_bytecode_path):
            shutil.rmtree(os.path.join(output_directory, 'bytecode'))
        shutil.copytree(os.path.join(root, 'bytecode'), output_bytecode_path)

        output_ink_path = os.path.join(output_directory, 'ink')
        if os.path.exists(output_ink_path):
            shutil.rmtree(os.path.join(output_directory, 'ink'))
        shutil.copytree(os.path.join(root, 'ink'), output_ink_path)

        shutil.copy(os.path.join(root, 'deps', 'mithril.min.js'), output_directory)
        shutil.copy(os.path.join(root, 'deps', 'tachyons.min.css'), output_directory)

        for result in results:
            result.settle()
        fout = context_stack.enter_context(open(os.path.join(output_directory, 'summary.json'), 'w'))
        write_json(fout, selected_drivers, bytecode_examples+ink_examples, results)

    # Re-add behind --verbose?
    #for r in results:
    #  if r.status is SuccessStatus:
    #    print('.', end='')
    #  elif r.status is FailStatus:
    #    print('F', end='')
    #  else:
    #    print('E', end='')

    #print('')
    #print(f"Test output has been generated to {args.out}")

    write_badges(results, output_directory)
    print(summarise_results(results))
    if args.serve:
      serve(args.out, args.serve)
    else:
      exit(decide_exit_status(results))

if __name__ == '__main__':
    root = os.path.dirname(os.path.abspath(__file__))
    sys.exit(main(root))
