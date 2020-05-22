#!/usr/bin/env python3
import sys
import os
import asyncio
import tempfile
import contextlib
import json
import shutil
import argparse

DEFAULT_OUT_PATH = os.path.abspath("out")
DEFAULT_TIMEOUT_S = 2
DEFAULT_COMPILER = "inklecate"
DEFAULT_RUNTIME = "inklecore"

class Status(object):
    def __init__(self, name, symbol, description):
        self.name = name
        self.symbol = symbol
        self.description = description

    def describe(self):
        return {"name": self.name,
                "description": self.description,
                "symbol": self.symbol,}

SuccessStatus = Status(
    "SUCCESS",
    "💚",
    ""
)

ErrorStatus = Status("ERROR", "❌", "Actual output does not match expected")
ErrorRuntimeCrashedStatus = Status("CRASHED", "🔥", "The runtime crashed on this input")
ErrorCompilerCrashedStatus = Status("CRASHED", "🔥", "The compiler crashed on this input")
TimeoutStatus = Status("TIMEOUT", "⌛", "The runtime timed out")
InfraErrorStatus = Status("INFRA_ERROR", "🏗️", "Infra error")

class PlayerResult(object):
    def __init__(self, program, example, player_job, diff_job):
        self.program = program
        self.example = example
        self.player_job = player_job
        self.diff_job = diff_job
        self.infra_error = None

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
                self.status = ErrorStatus
        else:
            self.status = SuccessStatus

    def describe(self):
        diff_path = os.path.relpath(self.diff_job.stdout_path, 'out')
        out_path = os.path.relpath(self.player_job.stdout_path, 'out')
        err_path = os.path.relpath(self.player_job.stderr_path, 'out')
        description = {
            "status": self.status.name,
            "program": self.program.name,
            "example": self.example.name,
            "diffPath": diff_path,
            "outPath": out_path,
            "errPath": err_path,
            "exitcode": self.player_job.return_code,
        }
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

    def settle(self):
        if self.compile_job.timed_out:
            self.status = TimeoutStatus
        elif self.compile_job.return_code:
            self.status = ErrorCompilerCrashedStatus
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
                self.status = ErrorStatus
        else:
            self.status = SuccessStatus

    def describe(self):
        diff_path = os.path.relpath(self.diff_job.stdout_path, 'out')
        out_path = os.path.relpath(self.player_job.stdout_path, 'out')
        err_path = os.path.relpath(self.player_job.stderr_path, 'out')

        compile_stdout_path = os.path.relpath(self.compile_job.stdout_path, 'out')
        compile_stderr_path = os.path.relpath(self.compile_job.stderr_path, 'out')
        compile_bytecode_path = os.path.relpath(self.compile_job.out_path, 'out')

        description = {
            "status": self.status.name,
            "program": self.compiler.name,
            "compiler": self.compiler.name,
            "runtime": self.runtime.name,
            "example": self.example.name,
            "diffPath": diff_path,
            "outPath": out_path,
            "errPath": err_path,
            "compileOutPath": compile_stdout_path,
            "compileErrPath": compile_stderr_path,
            "compileBytecodePath": compile_bytecode_path,
            "compilerExitcode": self.compile_job.return_code,
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

    def __lt__(self, o):
        return self.name < o.name

    def check(self):
        check_path(self.bytecode_path)

    def describe(self):
        source_path = os.path.relpath(self.bytecode_path)
        input_path = os.path.relpath(self.input_path)
        expected_path = os.path.relpath(self.transcript_path)
        with open(self.metadata_path) as f:
            metadata = json.load(f)
        return {
            "name": self.name,
            "sourcePath": source_path,
            "inputPath": input_path,
            "expectedPath": expected_path,
            "metadata": metadata,
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

    def __lt__(self, o):
        return self.name < o.name

    def describe(self):
        source_path = os.path.relpath(self.ink_path)
        input_path = os.path.relpath(self.input_path)
        expected_path = os.path.relpath(self.transcript_path)
        with open(self.metadata_path) as f:
            metadata = json.load(f)
        return {
            "name": self.name,
            "sourcePath": source_path,
            "inputPath": input_path,
            "expectedPath": expected_path,
            "metadata": metadata,
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
    names = set([name.split('.')[0] for name in files])
    examples = [BytecodeExample.fromDirAndName(folder, name) for name in names]
    return sorted(examples)

def find_all_ink_examples(root):
    folder = os.path.join(root, 'ink')
    files = os.listdir(folder)
    names = set([name.split('.')[0] for name in files])
    examples = [InkExample.fromDirAndName(folder, name) for name in names]
    return sorted(examples)

def find_all_player_drivers(root):
    folder = os.path.join(root, 'players')
    return sorted([
        PlayerDriver('inkjs', os.path.join(root, 'player_drivers', 'inkjs', 'player')),
        PlayerDriver('inklecore', os.path.join(root, 'player_drivers', 'inklecate', 'player')),
        PlayerDriver('test', os.path.join(root, 'player_drivers', 'test', 'player')),
    ])

def find_all_complier_drivers(root):
    return sorted([
        CompilerDriver("inklecate", os.path.join(root, 'drivers', 'inklecate_compiler_driver')),
    ])

class Job(object):
    def __init__(self, command, stdout_path=None, stderr_path=None, stdin_path=None, deps=None, timeout=DEFAULT_TIMEOUT_S):
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

    def begin(self):
        self.task = asyncio.create_task(self.run())

    async def run(self):
        if self.deps:
            done, pending = await asyncio.wait([dep.task for dep in self.deps])
        for dep in self.deps:
            if dep.return_code != 0:
                return
        fin = open(self.stdin_path) if self.stdin_path else None
        fout = open(self.stdout_path, 'wb') if self.stdout_path else None
        ferr = open(self.stderr_path, 'wb') if self.stderr_path else None
        print('Running "{}"'.format(' '.join(self.command)))
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

def name(*things, suffix=None):
    return '_'.join([thing.name for thing in things]) + suffix

def player_job(player, bytecode, output_directory, timeout, deps=None):
    stderr_path = os.path.join(output_directory, name(player, bytecode, suffix='_stderr.txt'))
    stdout_path = os.path.join(output_directory, name(player, bytecode, suffix='_stdout.txt'))
    return Job([player.path, bytecode.bytecode_path], stderr_path=stderr_path, stdout_path=stdout_path, stdin_path=bytecode.input_path, timeout=timeout, deps=deps)


def compile_player_job(compiler, player, example, bytecode_path, output_directory, timeout, deps=None):
    stderr_path = os.path.join(output_directory, name(compiler, player, example, suffix='_stderr.txt'))
    stdout_path = os.path.join(output_directory, name(compiler, player, example, suffix='_stdout.txt'))
    return Job([player.path, bytecode_path], stderr_path=stderr_path, stdout_path=stdout_path, stdin_path=example.input_path, timeout=timeout, deps=deps)

def compile_job(compiler, ink, output_directory, timeout):
    stderr_path = os.path.join(output_directory, name(compiler, ink, suffix='_stderr.txt'))
    stdout_path = os.path.join(output_directory, name(compiler, ink, suffix='_stdout.txt'))
    out_path = os.path.join(output_directory, name(compiler, ink, suffix='_out.json'))
    job = Job([compiler.path, "-o", out_path, ink.ink_path], stderr_path=stderr_path, stdout_path=stdout_path, timeout=timeout)
    job.out_path = out_path
    return job

def diff_job(a_path, b_path, out_path, deps=None):
    return Job(['diff', a_path, b_path], stdout_path=out_path, deps=deps)

def job_stats(jobs):
    total = 0
    done = 0
    for job in jobs:
        total += 1
        if job.task and job.task.done():
            done += 1
    return done, total

async def run_jobs(jobs):
    for job in jobs:
        job.begin()
    print(job_stats(jobs))
    done, pending = await asyncio.wait([job.task for job in jobs])
    print(job_stats(jobs))
    print(done)

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)
    return directory

def write_json(fout, runtimes, compilers, examples, results):
    metadata = {}
    statuses = {status.name: status.describe() for status in [
        ErrorStatus,
        SuccessStatus,
        TimeoutStatus,
        ErrorRuntimeCrashedStatus,
        ErrorCompilerCrashedStatus,
        InfraErrorStatus,
    ]}
    runtimes = [runtime.describe() for runtime in runtimes]
    compilers = [compiler.describe() for compiler in compilers]
    examples = [example.describe() for example in examples]
    results = [result.describe() for result in results]

    json.dump({
        "metadata": metadata,
        "statuses": statuses,
        "programs": compilers + runtimes,
        "examples": examples,
        "results": results,
    }, fout)

    # For each example
    # For each runner
    # One of the following end states:
    # - Infra error
    # - Timeout
    # - Player crashed
    # - Player output non-determnistic
    # - Player output diff from transcript
    # - Correct
    # statues
    # examples {name, description, inputs, expected output}
    # programs {name, etc}
    # result   {example index, program}

def main(root):
    bytecode_examples = find_all_bytecode_examples(root)
    ink_examples = find_all_ink_examples(root)
    compiler_drivers = find_all_complier_drivers(root)
    player_drivers = find_all_player_drivers(root)

    parser = argparse.ArgumentParser(description='Testing for Ink compilers and runtimes')
    parser.add_argument('--out', default=DEFAULT_OUT_PATH, help=f'output directory (default: {DEFAULT_OUT_PATH})')
    parser.add_argument('--list-drivers', action='store_true', help='list found drivers')
    parser.add_argument('--timeout', default=DEFAULT_TIMEOUT_S, type=int, help=f'timeout for subprocesses (default: {DEFAULT_TIMEOUT_S}s)')
    parser.add_argument('--reference-runtime', default=DEFAULT_RUNTIME, help=f'set the reference runtime (default: {DEFAULT_RUNTIME})')
    parser.add_argument('--reference-compiler', default=DEFAULT_COMPILER, help=f'set the reference compiler (default: {DEFAULT_COMPILER})')
    args = parser.parse_args()


    if args.reference_runtime not in [d.name for d in player_drivers]:
        runtimes = ", ".join([d.name for d in player_drivers])
        parser.error(f"Runtime '{args.reference_runtime}' unknown. Available runtimes: {runtimes}")
    if args.reference_compiler not in [d.name for d in compiler_drivers]:
        compilers = ", ".join([d.name for d in compiler_drivers])
        parser.error(f"Compiler '{args.reference_compiler}' unknown. Available compilers: {compilers}")

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
            for i, compiler in enumerate(compiler_drivers):
                job_a = compile_job(compiler, example, output_directory, args.timeout)
                job_b = compile_player_job(compiler, reference_runtime, example, job_a.out_path, output_directory, args.timeout, deps=[job_a])
                diff_path = os.path.join(output_directory, name(compiler, reference_runtime, example, suffix='_diff.txt'))
                job_c = diff_job(example.transcript_path, job_b.stdout_path, diff_path, deps=[job_b])
                jobs.extend([job_a, job_b, job_c])

                results.append(CompilerResult(compiler, reference_runtime, example, job_a, job_b, job_c))
                if compiler == reference_compiler:
                    refrence_compile_job[example] = job_a

        for j, example in enumerate(bytecode_examples):
            for i, player in enumerate(player_drivers):
                job_a = player_job(player, example, output_directory, args.timeout)
                diff_path = os.path.join(output_directory, name(player, example, suffix='_diff.txt'))
                job_b = diff_job(example.transcript_path, job_a.stdout_path, diff_path, deps=[job_a])
                jobs.append(job_a)
                jobs.append(job_b)
                results.append(PlayerResult(player, example, job_a, job_b))
        asyncio.run(run_jobs(jobs))

        for result in results:
            result.settle()
        fout = context_stack.enter_context(open(os.path.join(output_directory, 'summary.json'), 'w'))

        write_json(fout, player_drivers, compiler_drivers, bytecode_examples+ink_examples, results)
        shutil.copyfile(os.path.join(root, 'index.html'), os.path.join(output_directory, 'index.html'))

        output_bytecode_path = os.path.join(output_directory, 'bytecode')
        if os.path.exists(output_bytecode_path):
            shutil.rmtree(os.path.join(output_directory, 'bytecode'))
        shutil.copytree(os.path.join(root, 'bytecode'), output_bytecode_path)

        output_ink_path = os.path.join(output_directory, 'ink')
        if os.path.exists(output_ink_path):
            shutil.rmtree(os.path.join(output_directory, 'ink'))
        shutil.copytree(os.path.join(root, 'ink'), output_ink_path)

if __name__ == '__main__':
    root = os.path.dirname(os.path.abspath(__file__))
    sys.exit(main(root))
