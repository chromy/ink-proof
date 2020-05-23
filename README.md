# ink-proof
*Testing for [Ink](https://github.com/inkle/ink) compilers and runtimes*

[Ink](https//github.com/inkle/ink) is an open-source narrative scripting language created by [Inkle](https://www.inklestudios.com).
Users author stories as `.ink` [files](https://github.com/inkle/ink/blob/master/Documentation/WritingWithInk.md). Inkle provide a compiler (`inklecate`) which converts these `.ink` files to a [json based format](https://github.com/inkle/ink/blob/master/Documentation/ink_JSON_runtime_format.md) which is then interpreted by C# runtime.
There is at least one more largely complete implementation of the runtime: [inkjs](https://github.com/y-lohse/inkjs).

`ink-proof` is a tool for acceptance testing Ink compilers and runtimes.
It consists of a number of `.ink` and `.json` test cases.
Each test case contains an `.ink` or `.json` source file, an input file, and an expected output or "transcript" file.
`ink-proof` runs each test case against every runtime and compiler and compares the actual output to the expected output.
Results are generated as webpage for easy viewing.

The latest public run of `ink-proof` is available at https://chromy.github.io/ink-proof however you can also run the tool offline as follows:

```bash
git clone https://github.com/chromy/ink-proof.git
cd ink-proof
python3 proof.py
cd out
python3 -m http.server 8080
# Now navigate to http://localhost:8080
```

## Test cases
`ink-proof` has two kinds of test cases: ".ink" test cases and ".json" test cases.

Ink test cases live under the [ink](ink) directory. They consist of a `story.ink` file (for example [ink/I002/story.ink](ink/I002/story.ink)), an [input.txt](ink/I002/input.txt) file, an expected output file named [transcript.txt](ink/I002/transcript.txt), and some metadata in a file named [metadata.json](ink/I002/metadata.json).

Json test cases live under the [bytecode](bytecode) directory. 
They consist of a `.json` file (for example [bytecode/B005/story.json](bytecode/B005/story.json)), an [input.txt](bytecode/B005/input.txt) file, an expected output file named [transcript.txt](bytecode/B005/transcript.txt), and some metadata in a file named [metadata.json](bytecode/B005/metadata.json).

