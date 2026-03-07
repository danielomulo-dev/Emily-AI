import random
import logging
import asyncio

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
# TRIVIA QUESTION BANKS — WORLDWIDE GENERAL KNOWLEDGE
# ══════════════════════════════════════════════

MOVIE_TRIVIA = [
    {"q": "Which film won the first ever Academy Award for Best Picture in 1929?", "a": "Wings", "options": ["Wings", "Sunrise", "The Jazz Singer", "Ben-Hur"]},
    {"q": "Who directed 'Schindler's List' and 'Jurassic Park' in the same year (1993)?", "a": "Steven Spielberg", "options": ["Steven Spielberg", "Martin Scorsese", "James Cameron", "Ridley Scott"]},
    {"q": "What is the highest-grossing film of all time (not adjusted for inflation)?", "a": "Avatar", "options": ["Avatar", "Avengers: Endgame", "Titanic", "Star Wars: The Force Awakens"]},
    {"q": "In 'The Shawshank Redemption', what does Andy Dufresne crawl through to escape?", "a": "A sewage pipe", "options": ["A sewage pipe", "A tunnel", "An air vent", "An underground river"]},
    {"q": "'Parasite' (2019) became the first non-English film to win Best Picture. What country is it from?", "a": "South Korea", "options": ["South Korea", "Japan", "France", "Spain"]},
    {"q": "Who played the Joker in 'The Dark Knight' (2008)?", "a": "Heath Ledger", "options": ["Heath Ledger", "Joaquin Phoenix", "Jack Nicholson", "Jared Leto"]},
    {"q": "Which Studio Ghibli film features a girl trapped in a spirit world?", "a": "Spirited Away", "options": ["Spirited Away", "My Neighbor Totoro", "Princess Mononoke", "Howl's Moving Castle"]},
    {"q": "What fictional material is Captain America's shield made of?", "a": "Vibranium", "options": ["Vibranium", "Adamantium", "Uru", "Titanium"]},
    {"q": "Which Quentin Tarantino film is set during World War II?", "a": "Inglourious Basterds", "options": ["Inglourious Basterds", "Django Unchained", "Kill Bill", "The Hateful Eight"]},
    {"q": "Who was the first Black woman to win the Academy Award for Best Actress?", "a": "Halle Berry", "options": ["Halle Berry", "Whoopi Goldberg", "Viola Davis", "Lupita Nyong'o"]},
    {"q": "'Oldboy' (2003) is a cult classic film from which country?", "a": "South Korea", "options": ["South Korea", "Japan", "China", "Thailand"]},
    {"q": "In 'Inception', what object does Cobb use as his totem?", "a": "A spinning top", "options": ["A spinning top", "A dice", "A coin", "A chess piece"]},
    {"q": "What was the first Pixar feature film?", "a": "Toy Story (1995)", "options": ["Toy Story (1995)", "A Bug's Life (1998)", "Finding Nemo (2003)", "Monsters, Inc. (2001)"]},
    {"q": "Who directed 'Get Out' and 'Us'?", "a": "Jordan Peele", "options": ["Jordan Peele", "Spike Lee", "Ryan Coogler", "Barry Jenkins"]},
    {"q": "Which 1994 film follows a man sitting on a bench telling his life story to strangers?", "a": "Forrest Gump", "options": ["Forrest Gump", "The Shawshank Redemption", "Pulp Fiction", "The Green Mile"]},
    {"q": "In which film does a character say 'Here's looking at you, kid'?", "a": "Casablanca", "options": ["Casablanca", "Gone with the Wind", "The Maltese Falcon", "Citizen Kane"]},
    {"q": "Which country produces the most films per year?", "a": "India", "options": ["India", "United States", "China", "Nigeria"]},
    {"q": "What is the name of Thanos's home planet in the MCU?", "a": "Titan", "options": ["Titan", "Asgard", "Xandar", "Vormir"]},
    {"q": "Who composed the iconic score for 'Star Wars'?", "a": "John Williams", "options": ["John Williams", "Hans Zimmer", "Howard Shore", "Danny Elfman"]},
    {"q": "Which horror film features a family staying in an isolated hotel for the winter?", "a": "The Shining", "options": ["The Shining", "Psycho", "The Exorcist", "Hereditary"]},
]

FINANCE_TRIVIA = [
    {"q": "What does GDP stand for?", "a": "Gross Domestic Product", "options": ["Gross Domestic Product", "General Domestic Profit", "Global Development Plan", "Gross Dollar Percentage"]},
    {"q": "Which company was the first to reach a $1 trillion market cap?", "a": "Apple", "options": ["Apple", "Amazon", "Microsoft", "Saudi Aramco"]},
    {"q": "What is the world's most traded currency pair?", "a": "EUR/USD", "options": ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CNY"]},
    {"q": "The 2008 financial crisis was triggered by a crash in which market?", "a": "US housing/mortgage market", "options": ["US housing/mortgage market", "Stock market", "Oil market", "Tech sector"]},
    {"q": "What does IPO stand for?", "a": "Initial Public Offering", "options": ["Initial Public Offering", "Internal Profit Operation", "International Purchase Order", "Investor Portfolio Option"]},
    {"q": "Who is known as the 'Oracle of Omaha'?", "a": "Warren Buffett", "options": ["Warren Buffett", "Elon Musk", "Jeff Bezos", "George Soros"]},
    {"q": "What is Bitcoin's maximum supply?", "a": "21 million", "options": ["21 million", "100 million", "1 billion", "Unlimited"]},
    {"q": "What does a 'bear market' mean?", "a": "Prices are falling (decline of 20%+)", "options": ["Prices are falling (decline of 20%+)", "Prices are rising", "Market is stable", "Trading is halted"]},
    {"q": "Which stock exchange is the largest in the world by market capitalization?", "a": "New York Stock Exchange (NYSE)", "options": ["New York Stock Exchange (NYSE)", "NASDAQ", "London Stock Exchange", "Tokyo Stock Exchange"]},
    {"q": "What does ETF stand for?", "a": "Exchange-Traded Fund", "options": ["Exchange-Traded Fund", "Electronic Trading Facility", "Equity Transfer Fee", "Earnings Tax Form"]},
    {"q": "In which year did Bitcoin first launch?", "a": "2009", "options": ["2009", "2008", "2011", "2013"]},
    {"q": "What is the 'Rule of 72' used for?", "a": "Estimating how long it takes to double your money", "options": ["Estimating how long it takes to double your money", "Calculating retirement age", "Determining tax brackets", "Setting loan interest rates"]},
    {"q": "What does S&P in 'S&P 500' stand for?", "a": "Standard & Poor's", "options": ["Standard & Poor's", "Stocks & Portfolios", "Securities & Payments", "Savings & Profit"]},
    {"q": "What is a 'blue chip' stock?", "a": "A large, well-established, financially sound company", "options": ["A large, well-established, financially sound company", "A tech startup stock", "A penny stock", "A government bond"]},
    {"q": "What does inflation measure?", "a": "The rate at which prices for goods rise over time", "options": ["The rate at which prices for goods rise over time", "How much money a country has", "Stock market performance", "Currency exchange rates"]},
    {"q": "Who created Ethereum?", "a": "Vitalik Buterin", "options": ["Vitalik Buterin", "Satoshi Nakamoto", "Charles Hoskinson", "Changpeng Zhao"]},
    {"q": "What is 'diversification' in investing?", "a": "Spreading investments across different assets to reduce risk", "options": ["Spreading investments across different assets to reduce risk", "Putting all money in one stock", "Only investing in bonds", "Day trading"]},
    {"q": "The Dow Jones Industrial Average tracks how many companies?", "a": "30", "options": ["30", "100", "500", "50"]},
    {"q": "What is a 'hedge fund'?", "a": "A private investment fund using advanced strategies for high returns", "options": ["A private investment fund using advanced strategies for high returns", "A fund that only invests in agriculture", "A government savings program", "A type of insurance"]},
    {"q": "What does 'NASDAQ' stand for?", "a": "National Association of Securities Dealers Automated Quotations", "options": ["National Association of Securities Dealers Automated Quotations", "North American Stock Digital Automated Queue", "National Automated Securities Data And Quotes", "New American Standard Dollar And Quotes"]},
]

FOOD_TRIVIA = [
    {"q": "Which country is credited with inventing pizza?", "a": "Italy", "options": ["Italy", "Greece", "United States", "Turkey"]},
    {"q": "Sushi originated in which country?", "a": "Japan", "options": ["Japan", "China", "South Korea", "Thailand"]},
    {"q": "What is the most consumed fruit in the world?", "a": "Banana", "options": ["Banana", "Apple", "Mango", "Orange"]},
    {"q": "Which spice is the most expensive in the world by weight?", "a": "Saffron", "options": ["Saffron", "Vanilla", "Cardamom", "Cinnamon"]},
    {"q": "Kimchi is a fermented dish originating from which country?", "a": "South Korea", "options": ["South Korea", "Japan", "China", "Vietnam"]},
    {"q": "What is the main ingredient in guacamole?", "a": "Avocado", "options": ["Avocado", "Tomato", "Lime", "Jalapeño"]},
    {"q": "Which country is the largest producer of coffee in the world?", "a": "Brazil", "options": ["Brazil", "Colombia", "Ethiopia", "Vietnam"]},
    {"q": "What type of pastry is a croissant?", "a": "Laminated (layers of butter and dough)", "options": ["Laminated (layers of butter and dough)", "Choux", "Shortcrust", "Filo"]},
    {"q": "Pad Thai is a famous dish from which country?", "a": "Thailand", "options": ["Thailand", "Vietnam", "Indonesia", "Philippines"]},
    {"q": "What gives bread its holes?", "a": "Carbon dioxide from yeast fermentation", "options": ["Carbon dioxide from yeast fermentation", "Air beaten into dough", "Baking powder", "Steam from water"]},
    {"q": "Which nut is used to make marzipan?", "a": "Almond", "options": ["Almond", "Cashew", "Pistachio", "Walnut"]},
    {"q": "Paella is a traditional dish from which country?", "a": "Spain", "options": ["Spain", "Mexico", "Portugal", "Italy"]},
    {"q": "What is the hottest chili pepper in the world (as of 2024)?", "a": "Pepper X", "options": ["Pepper X", "Carolina Reaper", "Ghost Pepper", "Trinidad Scorpion"]},
    {"q": "The croissant actually originated in which country (before France adopted it)?", "a": "Austria", "options": ["Austria", "France", "Switzerland", "Belgium"]},
    {"q": "What is umami?", "a": "The fifth basic taste (savory/meaty)", "options": ["The fifth basic taste (savory/meaty)", "A Japanese soup", "A type of mushroom", "A cooking technique"]},
    {"q": "Couscous is a staple food from which region?", "a": "North Africa", "options": ["North Africa", "Middle East", "South Asia", "Southern Europe"]},
    {"q": "Which country invented chocolate (as a drink)?", "a": "Mexico (Aztec/Maya civilizations)", "options": ["Mexico (Aztec/Maya civilizations)", "Switzerland", "Belgium", "France"]},
    {"q": "What makes sourdough bread different from regular bread?", "a": "It uses a wild yeast starter instead of commercial yeast", "options": ["It uses a wild yeast starter instead of commercial yeast", "It uses more salt", "It's baked at a lower temperature", "It has no gluten"]},
    {"q": "Which country consumes the most tea per capita?", "a": "Turkey", "options": ["Turkey", "China", "United Kingdom", "India"]},
    {"q": "What is the world's most eaten food?", "a": "Rice", "options": ["Rice", "Wheat", "Corn", "Potatoes"]},
]


# ══════════════════════════════════════════════
# TRIVIA GAME ENGINE
# ══════════════════════════════════════════════

_active_games = {}

EMOJI_OPTIONS = ["🇦", "🇧", "🇨", "🇩"]
CATEGORY_NAMES = {
    "movie": "🎬 Movie Trivia",
    "finance": "💰 Finance Trivia",
    "food": "🍳 Food Trivia",
    "mixed": "🎲 Mixed Trivia",
}
CATEGORY_BANKS = {
    "movie": MOVIE_TRIVIA,
    "finance": FINANCE_TRIVIA,
    "food": FOOD_TRIVIA,
}


def get_trivia_question(category="mixed"):
    """Get a random trivia question."""
    if category == "mixed":
        bank = random.choice([MOVIE_TRIVIA, FINANCE_TRIVIA, FOOD_TRIVIA])
    else:
        bank = CATEGORY_BANKS.get(category, MOVIE_TRIVIA)

    question = random.choice(bank)
    options = question["options"][:]
    random.shuffle(options)
    correct_index = options.index(question["a"])

    return {
        "question": question["q"],
        "options": options,
        "correct_index": correct_index,
        "correct_answer": question["a"],
    }


def format_trivia_question(trivia, category, question_num=1, total=5):
    """Format a trivia question for Discord."""
    cat_name = CATEGORY_NAMES.get(category, "Trivia")
    lines = [f"**{cat_name} — Question {question_num}/{total}**\n"]
    lines.append(f"❓ {trivia['question']}\n")

    for i, option in enumerate(trivia["options"]):
        lines.append(f"{EMOJI_OPTIONS[i]} {option}")

    lines.append(f"\n_React with your answer! You have 15 seconds..._")
    return "\n".join(lines)


def start_game(guild_id, category="mixed", total_questions=5):
    """Initialize a trivia game for a guild."""
    _active_games[str(guild_id)] = {
        "category": category,
        "total": total_questions,
        "current": 0,
        "scores": {},
        "answered": set(),
    }
    return _active_games[str(guild_id)]


def get_game(guild_id):
    return _active_games.get(str(guild_id))


def record_answer(guild_id, user_id, is_correct):
    game = _active_games.get(str(guild_id))
    if not game:
        return
    if user_id not in game["scores"]:
        game["scores"][user_id] = 0
    if is_correct:
        game["scores"][user_id] += 1


def end_game(guild_id):
    return _active_games.pop(str(guild_id), None)


def format_scores(game):
    """Format final scores."""
    if not game or not game["scores"]:
        return "No one played! 😅"

    sorted_scores = sorted(game["scores"].items(), key=lambda x: -x[1])
    lines = ["🏆 **Final Scores:**\n"]

    for i, (user_id, score) in enumerate(sorted_scores):
        if i == 0:
            medal = "🥇"
        elif i == 1:
            medal = "🥈"
        elif i == 2:
            medal = "🥉"
        else:
            medal = f"**{i+1}.**"
        lines.append(f"{medal} <@{user_id}> — **{score}/{game['total']}**")

    if sorted_scores:
        top_score = sorted_scores[0][1]
        total = game["total"]
        if top_score == total:
            lines.append("\n*Perfect score! Absolute legend. 🔥*")
        elif top_score >= total * 0.8:
            lines.append("\n*Impressive! Someone knows their stuff! 📚*")
        elif top_score >= total * 0.5:
            lines.append("\n*Not bad! Room for improvement though. 😏*")
        else:
            lines.append("\n*Tough round! Try a different category? 😂*")

    return "\n".join(lines)
