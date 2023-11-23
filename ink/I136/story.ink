VAR loops_left = 100
VAR ace_of_hearts = 0
VAR ace_of_spades = 0
-> draw_cards

=== draw_cards
{loops_left == 0: -> end}
// every shuffle of the cards produces each card once (in a random order)
{shuffle:
- ~ ace_of_hearts++
- ~ ace_of_spades++
}
~ loops_left--
-> draw_cards

=== end
Hearts: {ace_of_hearts}
Spades: {ace_of_spades}
-> END
