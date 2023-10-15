#!/bin/env lua

local parser = require('pink.parser')
local runtime = require('pink.runtime')

function dump(o, indent)
  indent = indent or 1;
  local sp = function (ind)
      return (" "):rep(ind*2)
  end

  if type(o) == 'table' then
    local s = '{\n'
    for k,v in pairs(o) do
      if type(k) ~= 'number' then k = '"'..k..'"' end
      s = s .. sp(indent)..'['..k..'] = ' .. dump(v, indent+1) .. ',\n'
    end
    return s .. sp(indent-1) .. '}'
  elseif type(o) == 'string' then
    return "'"..(o:gsub("\\", "\\\\"):gsub("'", "\\'")).."'"
  else
    return tostring(o)
  end
end

function read(filename)
  local f = io.open(filename, "rb")
  if not f then error('failed to open "'..filename..'"') end
  local content = f:read("*all")
  f:close()
  return content
end

function write(filename, content)
  local f = io.open(filename, "wb")
  if not f then error('failed to open "'..filename..'"') end
  f:write(content)
  f:close()
end

function exists(filename)
  local f = io.open(filename, "r")
  return f ~= nil and io.close(f)
end


local action = arg[1]

if action == 'compile' then
  local inkFile = arg[2]
  local jsonFile = arg[3] -- needs to be created to indicate successful compilation
  local luaFile = jsonFile:gsub('.json', '_pink.lua') -- compile result
  write(luaFile, "return "..dump(parser(read(inkFile), inkFile)))
  write(jsonFile, "[\"json not supported\"]")

elseif action == 'run' then
  local jsonFile = arg[2]
  local reqLuaFile = jsonFile:gsub('.json', '_pink') -- compile result; .lua added when calling require

  if not exists(reqLuaFile..".lua") then
    io.stderr:write('only internal pink format supported\n')
    os.exit(1)
  end

  local story = runtime(require(reqLuaFile))

  while true do
    while story.canContinue do
      print(story.continue())
    end
    if #story.currentChoices == 0 then break end -- cannot continue and there are no choices
    print()
    for i = 1, #story.currentChoices do
      print(i .. ": " .. story.currentChoices[i].text)
    end
    -- TODO tags
    local answer = tonumber(io.read())
    if not answer or answer > #story.currentChoices then
      io.stderr:write('invalid answer '..tostring(answer)..'\n')
      os.exit(1)      
    end
    print ('?> '..story.currentChoices[answer].choiceText)
    story.chooseChoiceIndex(answer)
  end




end


