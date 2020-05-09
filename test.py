#!/usr/bin/env python3
import sys
import os
import asyncio
import tempfile
import contextlib
import json
import shutil


class Status(object):
    def __init__(self, name, symbol, description):
        self.name = name
        self.symbol = symbol
        self.description = description

    def describe(self):
        return {"name": self.name,
                "description": self.description,
                "symbol": self.symbol,}

SuccessStatus = Status("SUCCESS", "💚", "")
ErrorStatus = Status("ERROR", "❌", "")
CrashedStatus = Status("CRASHED", "🔥", "")
TimeoutStatus = Status("TIMEOUT", "⌛", "")

class PlayerResult(object):
    def __init__(self, program, example, player_job, diff_job):
        self.program = program
        self.example = example
        self.player_job = player_job
        self.diff_job = diff_job

    def settle(self):
        if self.player_job.return_code != 0:
            self.status = CrashedStatus
        elif self.diff_job.return_code == 1:
            self.status = ErrorStatus
        else:
            self.status = SuccessStatus

    def describe(self):
        return {
            "status": self.status.name,
            "player": self.program.name,
            "example": self.example.name,
            "diff": self.diff_job.stdout_path,
        }

class BytecodeExample(object):
    def __init__(self, name, bytecode_path, input_path, transcript_path):
        self.name = name
        self.bytecode_path = bytecode_path
        self.input_path = input_path
        self.transcript_path = transcript_path

    def __lt__(self, o):
        return self.name < o.name

    def describe(self):
        return {
            "name": self.name,
            "sourcePath": self.bytecode_path,
            "inputPath": self.input_path,
            "expectedOutputPath": self.transcript_path,
        }

    @staticmethod
    def fromDirAndName(root, name):
        bytecode_path = os.path.join(root, name + '.json')
        input_path = os.path.join(root, name + '.input')
        transcript_path = os.path.join(root, name + '.transcript')
        return BytecodeExample(name, bytecode_path, input_path, transcript_path)

class InkExample(object):
    def __init__(self, ink_path, input_path, transcript_path):
        self.ink_path = ink_path
        self.input_path = input_path
        self.transcript_path = transcript_path

    def __lt__(self, o):
        return self.name < o.name

    @staticmethod
    def fromDirAndName(root, name):
        ink_path = os.path.join(root, name + '.ink')
        input_path = os.path.join(root, name + '.input')
        transcript_path = os.path.join(root, name + '.transcript')
        return InkExample(ink_path, input_path, transcript_path)

class PlayerDriver(object):
    def __init__(self, name, path):
        self.name = name
        self.path = path

    def __lt__(self, o):
        return self.name < o.name

    def describe(self):
        return {
            "name": self.name,
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
        PlayerDriver('test', os.path.join(root, 'player_drivers', 'test', 'player')),
        ])

def find_all_complier_drivers(root):
    return []

class Job(object):
    def __init__(self, command, stdout_path=None, stderr_path=None, stdin_path=None, deps=None):
        self.command = command
        self.stdin_path = stdin_path
        self.stderr_path = stderr_path
        self.stdout_path = stdout_path
        self.task = None
        self.deps = deps if deps else []
        self.return_code = None

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
        process = await asyncio.create_subprocess_exec(self.command[0], *self.command[1:], stdout=fout, stderr=ferr, stdin=fin)
        self.return_code = await process.wait()
        if fout:
            fout.close()
        if ferr:
            ferr.close()
        if fin:
            fin.close()

def name(*things, suffix=None):
    return '_'.join([thing.name for thing in things]) + suffix

def player_job(player, bytecode, output_directory):
    stderr_path = os.path.join(output_directory, name(player, bytecode, suffix='_stderr.txt'))
    stdout_path = os.path.join(output_directory, name(player, bytecode, suffix='_stdout.txt'))
    return Job([player.path, bytecode.bytecode_path], stderr_path=stderr_path, stdout_path=stdout_path, stdin_path=bytecode.input_path)

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

def write_json(fout, programs, examples, results):
    metadata = {}
    statuses = {status.name: status.describe() for status in [
        ErrorStatus, SuccessStatus, TimeoutStatus, CrashedStatus]}
    programs = [program.describe() for program in programs]
    examples = [example.describe() for example in examples]
    results = [result.describe() for result in results]

    json.dump({
        "metadata": metadata,
        "statuses": statuses,
        "programs": programs,
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
    with contextlib.ExitStack() as context_stack:
        # output_directory = context_stack.enter_context(tempfile.TemporaryDirectory())
        output_directory = ensure_dir('out')

        jobs = []
        results = []
        for i, player in enumerate(player_drivers):
            for j, example in enumerate(bytecode_examples):
                job_a = player_job(player, example, output_directory)
                diff_path = os.path.join(output_directory, name(player, example, suffix='_diff.txt'))
                job_b = diff_job(example.transcript_path, job_a.stdout_path, diff_path, deps=[job_a])
                jobs.append(job_a)
                jobs.append(job_b)
                results.append(PlayerResult(player, example, job_a, job_b))
        asyncio.run(run_jobs(jobs))

        for result in results:
            result.settle()
        fout = context_stack.enter_context(open(os.path.join(output_directory, 'summary.json'), 'w'))

        write_json(fout, player_drivers, bytecode_examples, results)
        shutil.copyfile(os.path.join(root, 'index.html'), os.path.join(output_directory, 'index.html'))

        output_bytecode_path = os.path.join(output_directory, 'bytecode')
        if os.path.exists(output_bytecode_path):
            shutil.rmtree(os.path.join(output_directory, 'bytecode'))
        shutil.copytree(os.path.join(root, 'bytecode'), output_bytecode_path)



if __name__ == '__main__':
    root = os.path.dirname(os.path.abspath(__file__))
    sys.exit(main(root))
