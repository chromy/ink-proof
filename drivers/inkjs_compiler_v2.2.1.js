#!/usr/local/bin/node

var Compiler = require('../deps/inkjs_v2.2.1').Compiler;
var fs = require('fs');
var bytecodePath = process.argv[3];
var inkPath = process.argv[4];
var ink = fs.readFileSync(inkPath, 'UTF-8');
var bytecode = (new Compiler(ink)).Compile().ToJson();
fs.writeFileSync(bytecodePath, bytecode, 'UTF-8');
