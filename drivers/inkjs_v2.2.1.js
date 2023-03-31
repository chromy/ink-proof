#!/usr/local/bin/node

var Story = require('../deps/inkjs_v2.2.1').Story;
var fs = require('fs');
var bytecodePath = process.argv[2];
var bytecode = fs.readFileSync(bytecodePath, 'UTF-8').replace(/^\uFEFF/, '');
var story = new Story(bytecode);

var input = "";

process.stdin.setEncoding('utf8');
process.stdin.on('readable', () => {
  let chunk;
  while ((chunk = process.stdin.read()) !== null) {
    input += chunk;
  }
});
process.stdin.on('end', () => {
  const choices = input.split("\n").map(n => parseInt(n, 10) - 1);
  let lastText = "";
  while (story.canContinue || story.currentChoices.length > 0) {
    if (story.currentChoices.length > 0) {
      process.stdout.write("\n");
      for (let i=0; i<story.currentChoices.length; ++i) {
        const choice = story.currentChoices[i];
        process.stdout.write(`${i+1}: ${choice.text}\n`);
      }
      process.stdout.write("?> ");
      const choiceIndex = choices.shift();
      story.ChooseChoiceIndex(choiceIndex);
    }

    if (story.currentTags.length) {
      lastText = "# tags: " + story.currentTags.join(", ");
      process.stdout.write(lastText);
    }

    lastText = story.ContinueMaximally();
    process.stdout.write(lastText);

    if (story.currentTags.length) {
      lastText = "# tags: " + story.currentTags.join(", ");
      process.stdout.write(lastText);
    }
  }

});

