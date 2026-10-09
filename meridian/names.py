"""Word lists for the name heuristics in redact.py.

Written independently of the synthetic data generator, so measured name
recall reflects real coverage rather than a shared list.
"""

_FIRST = """
Aaron Abdul Abigail Adam Adebayo Adriana Ahmed Aisha Alan Albert Alejandro Alexander Alexandra Alexis Ali Alice Alicia
Allison Alma Alvin Amanda Amber Amir Amy Ana Andre Andrea Andrew Angela Angelica Anita Ann Anna Anne Anthony Antonio
Arjun Arthur Ashley Atsuko Audrey Barbara Beatriz Benjamin Bernard Beth Betty Beverly Bianca Bilal Bogdan Bonnie Brandon
Brenda Brian Brittany Bruce Bryan Camila Carl Carlos Carmen Carol Caroline Catherine Cecilia Chad Chang Charles Charlotte
Chen Cheryl Chloe Christian Christina Christine Christopher Cindy Claire Claudia Connie Courtney Craig Cristina Crystal
Cynthia Daniel Danielle Darius David Deborah Debra Deepak Denise Dennis Derek Devon Diana Diane Diego Dmitri Dolores
Donald Donna Doris Dorothy Douglas Dylan Earl Edgar Edith Eduardo Edward Eileen Elaine Elena Elijah Elizabeth Ellen
Emily Emma Enrique Eric Erica Erik Esther Ethan Eugene Evelyn Fatima Felipe Fernando Fiona Florence Frances Francisco
Frank Gabriel Gabriela Gail Gary Gerald Gilberto Gina Gloria Gregory Guadalupe Gustavo Hannah Harold Harry Hassan Heather
Hector Helen Henry Hiroshi Howard Hussein Ian Ibrahim Igor Irene Isaac Isabel Isabella Ivan Jacob Jacqueline James Jamal
Janet Janice Jared Jasmine Jason Javier Jeffrey Jennifer Jeremy Jerome Jesse Jessica Jesus Joan Joanne Joel John Jonathan
Jorge Jose Joseph Joshua Joyce Juan Judith Judy Julia Julian Julie Justin Karen Katherine Kathleen Kathryn Kayla Keith
Kelly Kenji Kenneth Kevin Kiara Kimberly Kwame Kyle Larry Latisha Laura Lauren Lawrence Leah Leonard Leslie Liam Linda
Lisa Lorraine Louis Lucia Luis Luz Lydia Madison Malik Manuel Marcus Margaret Maria Marie Marilyn Mario Marisol Marjorie
Martha Martin Mary Matthew Megan Mei Melissa Michael Michelle Miguel Mildred Min Mohammed Monica Muhammad Nadia Nancy
Natalie Nathan Nicholas Nicole Nikhil Nina Noah Norma Oksana Olga Oliver Olivia Omar Oscar Pablo Pamela Patricia Paul
Paula Pedro Peggy Peter Phyllis Pierre Priya Rachel Rafael Rahul Ralph Ramon Randy Raquel Raymond Rebecca Regina Ricardo
Richard Rita Robert Roberto Rodrigo Roger Ronald Rosa Rosalind Roxanne Russell Ruth Ryan Samantha Samuel Sandra Sanjay
Sara Sarah Scott Sean Sergei Sharon Shirley Sofia Sophia Stephanie Stephen Steven Susan Tamara Tanya Teresa Theresa
Thomas Timothy Tomas Tracy Tyler Valerie Vanessa Victor Victoria Vincent Virginia Walter Wanda Wei Wendy William Xavier
Yolanda Yusuf Zachary Zainab Zoe
"""
COMMON_FIRST_NAMES = frozenset(_FIRST.split())

# First names that are also ordinary English words. Only treated as a name
# when followed by a capitalized surname or after a cue ("my son Will").
AMBIGUOUS_FIRST_NAMES = frozenset("""
Will Mark Bill Rose Grace Hope Faith Joy May June April Art Sue Pat Rich Chase Dawn Earl Ray Jack Frank Victor Ruby
Pearl Iris Lily Violet Amber Crystal Sunny Bob Rob Sky Summer Autumn Penny Holly Ivy Max Gene Don Ken Gail Dale Lane
Grant Wade Drew Miles Reed Hunter Jay Joy Kim Lee Les Mo Ali
""".split())

# Lowercased words that are capitalized in normal writing but are not names:
# calendar words, places, institutions, brands, products, CSR shorthand.
KEEP_CAPITALIZED = frozenset(w.lower() for w in """
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september
october november december christmas thanksgiving easter labor memorial independence
meridian harbor bank branch customer service services support center centre department team office online mobile app
banking checking savings visa mastercard amex discover zelle venmo paypal cashapp apple google samsung android iphone
amazon walmart target costco netflix spotify uber lyft doordash ebay etsy hulu disney verizon comcast xfinity spectrum
geico progressive statefarm shell exxon mobil sunoco wawa stewarts starbucks dunkin mcdonalds kroger safeway shoprite
hannaford wegmans cvs walgreens rite home depot lowes bestbuy ikea
cfpb fdic occ ftc irs ssa ssi snap medicare medicaid equifax experian transunion fico
new york ny nj ct pa ma vt jersey connecticut pennsylvania massachusetts vermont hudson valley dutchess ulster westchester
putnam orange albany poughkeepsie kingston beacon newburgh fishkill wappingers rhinebeck millbrook brooklyn manhattan
queens bronx route rt highway main street avenue road
english spanish español american mexican chinese indian korean russian haitian dominican puerto rican
mr mrs ms dr sir madam ma'am dear hello hi hey thanks regards sincerely subject re fw fwd attn
cust cx csr adv advd adv'd esc supv sup acct acct# pls plz w/ b/c bc re: tx txn trans dept mgr bal amt avail
ach atm pin otp nsf od apr heloc cd ira
yes no ok okay please thank god lord
spanish-speaking
""".split())
