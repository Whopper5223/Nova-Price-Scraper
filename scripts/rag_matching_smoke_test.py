"""
Live smoke test for RAG-based product matching: Stage 1/2 (matches_query +
filter_by_relevance) and Stage 3 (product_matcher's LLM-judge).

Run on the server, where nomic-embed-text + the chat model are pulled and
Ollama is reachable:
    python3 scripts/rag_matching_smoke_test.py

Unlike tests/test_product_embeddings.py / tests/test_product_matcher.py
(hand-built vectors / mocked chat_json, no live model), this hits real Ollama.
Stage 1/2 prints raw similarity scores per candidate; Stage 3 prints the
judge's actual pick per case -- keep running both whenever the stemmer,
DEFAULT_RELATIVE_MARGIN, or product_matcher's prompt changes, since none of
that is meaningfully unit-testable against a real model's judgment.

Stage 1/2 cases: a query, candidate product names, and which names SHOULD
survive both stages. Stage 3 cases: a recipe name + full ingredient list (the
disambiguating context), one ingredient, its candidates, and which candidate
name the judge SHOULD pick (or None if none of them should be accepted).
PASS/FAIL is against that expectation; raw output is printed regardless so a
FAIL can be diagnosed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper import ollama_client, product_embeddings as pe, product_matcher

PASS, FAIL = "PASS", "FAIL"

# (query, candidate names, expected survivors)
CASES = [
    # --- flavor / variant noise ---
    ("butter", ["Salted Butter", "Butter Cookies"], ["Salted Butter"]),
    ("milk", ["Whole Milk", "Chocolate Milk", "Almond Milk"], ["Whole Milk"]),
    ("orange juice", ["Orange Juice", "Apple Juice", "Orange Soda"], ["Orange Juice"]),
    ("cheddar cheese", ["Cheddar Cheese", "Cheese Puffs", "Cheese Crackers"], ["Cheddar Cheese"]),
    ("white bread", ["White Bread", "Garlic Bread", "Breadsticks"], ["White Bread"]),
    ("white rice", ["White Rice", "Rice Krispies Cereal", "Rice Cakes"], ["White Rice"]),
    ("potato chips", ["Potato Chips", "Chocolate Chips"], ["Potato Chips"]),
    ("vanilla yogurt", ["Vanilla Yogurt", "Strawberry Yogurt", "Greek Yogurt"], ["Vanilla Yogurt"]),

    # --- substring-in-longer-word (lexical layer alone should already fix these) ---
    ("egg", ["Large Eggs, Dozen", "Eggplant"], ["Large Eggs, Dozen"]),
    ("pea", ["Frozen Peas", "Peanut Butter"], ["Frozen Peas"]),
    ("ham", ["Sliced Ham", "Hamburger Buns"], ["Sliced Ham"]),
    ("corn", ["Sweet Corn", "Popcorn, Butter Flavor"], ["Sweet Corn"]),
    ("apple", ["Gala Apples", "Pineapple Chunks"], ["Gala Apples"]),
    ("nut", ["Mixed Nuts", "Coconut Water"], ["Mixed Nuts"]),

    # --- singular / plural ---
    ("onion", ["Onions, 3lb Bag"], ["Onions, 3lb Bag"]),
    ("carrot", ["Baby Carrots"], ["Baby Carrots"]),
    ("tomato", ["Fresh Tomatoes", "Tomato Sauce"], ["Fresh Tomatoes", "Tomato Sauce"]),
    ("cookie", ["Butter Cookies", "Cookie Dough Ice Cream"], ["Butter Cookies", "Cookie Dough Ice Cream"]),

    # --- multi-word / order-sensitive, including legit variants that SHOULD survive ---
    ("chicken broth", ["Chicken Broth, 32oz", "Chicken Breast Tenders", "Broth Flavored Chicken Chips"],
     ["Chicken Broth, 32oz"]),
    ("chicken broth", ["Chicken Broth, 32oz", "Chicken Bouillon Broth Mix"],
     ["Chicken Broth, 32oz", "Chicken Bouillon Broth Mix"]),
    ("apple juice", ["Apple Juice", "Apple Cinnamon Juice Blend"],
     ["Apple Juice", "Apple Cinnamon Juice Blend"]),
]


def run():
    results = []
    for query, names, expected in CASES:
        candidates = [{"name": n} for n in names]
        lexical = [c for c in candidates if pe.matches_query(c["name"], query)]

        scores = {}
        if lexical:
            try:
                query_embedding = ollama_client.embed(f"search_query: {query}")
                for c in lexical:
                    sim = pe.cosine_similarity(query_embedding, pe.get_embedding(c["name"]))
                    scores[c["name"]] = sim
            except Exception as e:
                print(f"  [warn] could not compute diagnostic scores: {e}")

        final = [c["name"] for c in pe.filter_by_relevance(query, lexical)]
        passed = set(final) == set(expected)
        results.append((query, names, expected, final, scores, passed))

    print(f"\n{sum(r[5] for r in results)}/{len(results)} cases matched expectations\n")
    for query, names, expected, final, scores, passed in results:
        status = PASS if passed else FAIL
        print(f"[{status}] {query!r} over {names}")
        if scores:
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            print("       scores:   " + ", ".join(f"{n}={s:.4f}" for n, s in ranked))
        print(f"       expected: {expected}")
        print(f"       got:      {final}")
        print()


# (recipe_name, full ingredient list, the one ingredient being judged,
#  candidate product names, expected pick -- or None if none should be accepted)
#
# This battery was generated across 5 lenses (context-flip, lexical-decoy,
# explicit-rejection, plain-easy, realistic-recipes) and every case's expected
# answer was independently re-derived, blind, by two separate reviewers before
# being kept -- a case only survives if both agree with each other. An earlier,
# hand-written attempt at this got one case wrong (a "Chocolate Milkshake"
# case expected "Chocolate Milk" for its "milk" ingredient, but the recipe
# already had a separate "chocolate syrup" ingredient, which makes plain milk
# the actually-correct pick) -- several cases below explicitly exercise that
# same "does something else in the list already cover this variant" check
# (e.g. apple cider vs. apple cider vinegar, unsalted vs. salted butter).
STAGE3_CASES = [
    # --- context-flip: same/similar candidates, opposite recipe context ---
    (
        "Key Lime Pie",
        ["graham cracker crust", "milk", "key lime juice", "egg yolks", "whipped cream"],
        "milk",
        ["Eagle Brand Sweetened Condensed Milk, 14 oz", "Organic Valley Whole Milk, 1 Gallon",
         "Carnation Evaporated Milk, 12 oz", "Hershey's Chocolate Milk, 1/2 Gallon"],
        "Eagle Brand Sweetened Condensed Milk, 14 oz",
    ),
    (
        "Creamy Mashed Potatoes",
        ["potatoes", "milk", "butter", "salt", "pepper"],
        "milk",
        ["Eagle Brand Sweetened Condensed Milk, 14 oz", "Organic Valley Whole Milk, 1 Gallon",
         "Carnation Evaporated Milk, 12 oz", "Hershey's Chocolate Milk, 1/2 Gallon"],
        "Organic Valley Whole Milk, 1 Gallon",
    ),
    (
        "Vegetarian Minestrone Soup",
        ["broth", "diced tomatoes", "kidney beans", "carrots", "celery", "pasta", "olive oil"],
        "broth",
        ["Swanson Chicken Broth, 32 oz", "Kitchen Basics Vegetable Broth, 32 oz",
         "Progresso Beef Broth, 14.5 oz", "Bar Harbor Clam Broth, 8 oz"],
        "Kitchen Basics Vegetable Broth, 32 oz",
    ),
    (
        "Classic Chicken Noodle Soup",
        ["chicken breast", "broth", "carrots", "celery", "egg noodles", "onion"],
        "broth",
        ["Swanson Chicken Broth, 32 oz", "Kitchen Basics Vegetable Broth, 32 oz",
         "Progresso Beef Broth, 14.5 oz", "Bar Harbor Clam Broth, 8 oz"],
        "Swanson Chicken Broth, 32 oz",
    ),
    (
        "Homemade Butterscotch Sauce",
        ["sugar", "butter", "heavy cream", "vanilla extract", "salt"],
        "sugar",
        ["Domino Granulated Sugar, 4 lb", "Domino Light Brown Sugar, 2 lb",
         "C&H Powdered Sugar, 2 lb", "Splenda Granulated Sweetener, 9.7 oz"],
        "Domino Light Brown Sugar, 2 lb",
    ),
    (
        "Classic Sugar Cookies",
        ["flour", "sugar", "butter", "eggs", "vanilla extract", "baking powder"],
        "sugar",
        ["Domino Granulated Sugar, 4 lb", "Domino Light Brown Sugar, 2 lb", "Splenda Granulated Sweetener, 9.7 oz"],
        "Domino Granulated Sugar, 4 lb",
    ),
    (
        "Vegan Chocolate Chip Cookies",
        ["flour", "sugar", "vegan butter", "flax egg", "vegan chocolate chips", "baking soda", "vanilla extract"],
        "vegan butter",
        ["Land O'Lakes Salted Butter, 1 lb", "Kerrygold Pure Irish Butter, 8 oz",
         "Challenge Unsalted Butter, 1 lb", "Organic Valley Salted Butter, 1 lb"],
        None,
    ),
    (
        "Coq au Vin",
        ["chicken thighs", "wine", "bacon lardons", "pearl onions", "mushrooms", "chicken broth", "thyme"],
        "wine",
        ["Barefoot Cabernet Sauvignon, 750 mL", "Gallo Sweet Moscato, 750 mL", "Taylor Cream Sherry, 750 mL",
         "Ariel Non-Alcoholic Cabernet Sauvignon, 750 mL", "Ariel Non-Alcoholic White Wine, 750 mL"],
        "Barefoot Cabernet Sauvignon, 750 mL",
    ),

    # --- lexical-decoy: a candidate shares words with the ingredient but is the wrong product ---
    (
        "Beef and Broccoli Stir Fry",
        ["beef sirloin", "broccoli florets", "soy sauce", "garlic", "fresh ginger", "vegetable oil", "jasmine rice"],
        "soy sauce",
        ["Soy Sauce, 15 fl oz", "Silk Organic Unsweetened Soy Milk, 64 fl oz",
         "Lea & Perrins Worcestershire Sauce, 10 fl oz", "Panda Brand Oyster Sauce, 18 oz"],
        "Soy Sauce, 15 fl oz",
    ),
    (
        "Chocolate Chip Cookies",
        ["flour", "baking soda", "salt", "butter", "brown sugar", "eggs", "vanilla extract", "chocolate chips"],
        "baking soda",
        ["Baking Powder", "Club Soda", "Great Value Baking Soda, 1 lb", "Corn Starch"],
        "Great Value Baking Soda, 1 lb",
    ),
    (
        "Fresh Guacamole",
        ["avocado", "lime juice", "red onion", "cilantro", "jalapeno", "salt", "diced tomato"],
        "lime juice",
        ["Lime Juice, 8 fl oz", "Minute Maid Frozen Limeade Concentrate, 12 fl oz",
         "Key Lime Yogurt, 5.3 oz", "Lemon Juice, 8 fl oz"],
        "Lime Juice, 8 fl oz",
    ),
    (
        "Homemade Pancakes",
        ["flour", "baking powder", "salt", "sugar", "milk", "eggs", "vanilla extract", "butter"],
        "vanilla extract",
        ["McCormick Pure Vanilla Extract, 2 fl oz", "Breyers Vanilla Ice Cream",
         "Nielsen-Massey Vanilla Bean Paste", "Silk Vanilla Almond Milk"],
        "McCormick Pure Vanilla Extract, 2 fl oz",
    ),
    (
        "Slow Cooker Apple Cider Pulled Pork",
        ["pork shoulder", "apple cider", "yellow onion", "garlic cloves", "apple cider vinegar", "brown sugar", "salt"],
        "apple cider",
        ["Apple Cider Vinegar, 16 fl oz", "Mott's Apple Juice, 64 fl oz",
         "Martinelli's Sparkling Apple Cider, 25.4 fl oz", "Musselman's Apple Cider, 64 fl oz"],
        "Musselman's Apple Cider, 64 fl oz",
    ),
    (
        "Thai Coconut Curry Soup",
        ["chicken breast", "coconut milk", "red curry paste", "fish sauce", "lime juice", "cilantro", "mushrooms"],
        "coconut milk",
        ["Coconut Milk, 13.5 oz Can", "Vita Coco Pure Coconut Water, 1 L",
         "Coco Lopez Cream of Coconut, 15 oz", "Almond Breeze Almond Milk, 64 fl oz"],
        "Coconut Milk, 13.5 oz Can",
    ),
    (
        "Southern Buttermilk Biscuits",
        ["flour", "baking powder", "baking soda", "salt", "cold butter", "buttermilk"],
        "buttermilk",
        ["Land O'Lakes Butter", "Whole Milk", "Buttermilk", "Heavy Cream"],
        "Buttermilk",
    ),
    (
        "Classic Beef Chili",
        ["ground beef", "kidney beans", "diced tomatoes", "chili powder", "ground cumin", "yellow onion", "garlic"],
        "chili powder",
        ["Chili Powder, 2.5 oz", "Huy Fong Chili Garlic Sauce, 8 oz",
         "Hormel Chili with Beans, 15 oz", "Tajin Chile Lime Seasoning, 5 oz"],
        "Chili Powder, 2.5 oz",
    ),

    # --- explicit-rejection: none of the candidates are actually right ---
    (
        "Truffle Mushroom Risotto",
        ["Arborio rice", "Cremini mushrooms", "Vegetable broth", "Parmesan cheese", "Truffle oil", "Dry white wine", "Unsalted butter"],
        "Truffle oil",
        ["Filippo Berio Extra Virgin Olive Oil, 25.5oz", "Garlic Infused Olive Oil", "Pompeian Robust Extra Virgin Olive Oil"],
        None,
    ),
    (
        "Homemade Corn Tortillas",
        ["Masa harina", "Warm water", "Salt", "Vegetable oil for griddle"],
        "Masa harina",
        ["Bob's Red Mill Corn Flour", "Quaker Yellow Corn Meal", "Argo Corn Starch"],
        None,
    ),
    (
        "Pad Thai",
        ["Rice noodles", "Tamarind paste", "Fish sauce", "Shrimp", "Bean sprouts", "Eggs", "Roasted peanuts", "Lime wedges"],
        "Tamarind paste",
        ["Heinz Apple Cider Vinegar", "Nakano Rice Vinegar", "Lea & Perrins Worcestershire Sauce"],
        None,
    ),
    (
        "Thai Green Curry",
        ["Chicken breast", "Green curry paste", "Coconut milk", "Kaffir lime leaves", "Thai basil", "Bamboo shoots"],
        "Kaffir lime leaves",
        ["McCormick Bay Leaves", "Badia Dried Lime Peel", "Traditional Medicinals Lemon Verbena Tea Bags"],
        None,
    ),
    (
        "Korean Beef Bibimbap",
        ["Cooked white rice", "Spinach", "Julienned carrots", "Bean sprouts", "Ground beef", "Fried egg", "Gochujang"],
        "Gochujang",
        ["Huy Fong Sriracha Hot Sauce", "Frank's RedHot Original", "Mae Ploy Sweet Chili Sauce"],
        None,
    ),
    (
        "Miso Glazed Salmon",
        ["Salmon fillets", "Miso paste", "Soy sauce", "Mirin", "Brown sugar", "Scallions"],
        "Miso paste",
        ["Better Than Bouillon Vegetable Base", "Trader Joe's Tahini", "Kikkoman Hoisin Sauce"],
        None,
    ),
    (
        "Fattoush Salad",
        ["Romaine lettuce", "Cucumber", "Tomato", "Radish", "Toasted pita chips", "Sumac", "Olive oil", "Lemon juice"],
        "Sumac",
        ["McCormick Paprika, 2.12oz", "McCormick Chili Powder, 2.5oz", "Simply Organic Smoked Paprika"],
        None,
    ),
    (
        "Moroccan Harissa Chicken",
        ["Chicken thighs", "Harissa paste", "Couscous", "Chickpeas", "Lemon", "Garlic", "Ground cumin"],
        "Harissa paste",
        ["Tabasco Original Red Sauce", "Frank's RedHot Original Cayenne Pepper Sauce", "Cholula Original Hot Sauce"],
        None,
    ),

    # --- plain-easy: an unambiguous pick among plausible-looking inferior options ---
    (
        "Chicken Noodle Soup",
        ["chicken breast", "egg noodles", "carrots", "celery", "onion", "chicken broth"],
        "egg noodles",
        ["Barilla Egg Noodles, 12 oz", "Barilla Spaghetti, 16 oz", "Barilla Lasagna Noodles, 16 oz"],
        "Barilla Egg Noodles, 12 oz",
    ),
    (
        "Buttermilk Pancakes",
        ["all-purpose flour", "buttermilk", "eggs", "baking soda", "sugar", "salt", "unsalted butter"],
        "unsalted butter",
        ["Land O'Lakes Unsalted Butter, 1 lb", "Land O'Lakes Salted Butter, 1 lb", "Crisco Butter-Flavored Shortening, 1 lb"],
        "Land O'Lakes Unsalted Butter, 1 lb",
    ),
    (
        "Beef Tacos",
        ["ground beef", "taco seasoning", "corn tortillas", "shredded cheese", "lettuce", "tomato", "sour cream"],
        "ground beef",
        ["Kroger 80/20 Ground Beef, 1 lb", "Jennie-O Ground Turkey, 1 lb", "Beyond Meat Plant-Based Ground, 14 oz"],
        "Kroger 80/20 Ground Beef, 1 lb",
    ),
    (
        "Beef and Broccoli Stir Fry",
        ["beef sirloin", "soy sauce", "broccoli", "garlic", "ginger", "sesame oil", "cornstarch"],
        "soy sauce",
        ["Kikkoman Soy Sauce, 15 oz", "Kikkoman Teriyaki Marinade & Sauce, 10 oz", "Red Boat Fish Sauce, 8.45 oz"],
        "Kikkoman Soy Sauce, 15 oz",
    ),
    (
        "Greek Salad",
        ["cucumber", "tomato", "red onion", "feta cheese", "kalamata olives", "olive oil", "oregano"],
        "feta cheese",
        ["Athenos Traditional Feta Cheese Block, 8 oz", "Great Value Shredded Cheddar Cheese, 8 oz", "Philadelphia Cream Cheese, 8 oz"],
        "Athenos Traditional Feta Cheese Block, 8 oz",
    ),
    (
        "Chicken Alfredo",
        ["fettuccine pasta", "chicken breast", "heavy cream", "parmesan cheese", "butter", "garlic"],
        "heavy cream",
        ["Land O'Lakes Heavy Whipping Cream, 1 pint", "Great Value Fat-Free Skim Milk, 1 gallon", "Eagle Brand Sweetened Condensed Milk, 14 oz"],
        "Land O'Lakes Heavy Whipping Cream, 1 pint",
    ),
    (
        "Classic Guacamole",
        ["avocado", "lime juice", "red onion", "cilantro", "jalapeno", "salt"],
        "avocado",
        ["Hass Avocado, each", "Chosen Foods 100% Pure Avocado Oil, 16 oz", "Wholly Guacamole Classic Dip, 8 oz"],
        "Hass Avocado, each",
    ),
    (
        "Chili Con Carne",
        ["ground beef", "kidney beans", "diced tomatoes", "chili powder", "onion", "garlic"],
        "kidney beans",
        ["Bush's Kidney Beans, 15 oz can", "Bush's Black Beans, 15 oz can", "Bush's Baked Beans, 15 oz can"],
        "Bush's Kidney Beans, 15 oz can",
    ),

    # --- realistic-recipes: common dishes, larger ingredient lists ---
    (
        "Classic Spaghetti Carbonara",
        ["spaghetti", "pancetta", "large eggs", "Pecorino Romano cheese", "freshly ground black pepper", "garlic clove"],
        "pancetta",
        ["Boar's Head Pancetta, Sliced, 4 oz", "Oscar Mayer Naturally Hardwood Smoked Bacon, 16 oz",
         "Kroger Diced Cooked Ham, 8 oz", "Hormel Pepperoni, 5 oz"],
        "Boar's Head Pancetta, Sliced, 4 oz",
    ),
    (
        "Chili Con Carne",
        ["ground beef", "kidney beans", "diced tomatoes", "onion", "garlic", "chili powder", "cumin", "beef broth"],
        "chili powder",
        ["McCormick Chili Powder, 2.5 oz", "McCormick Ground Cayenne Pepper, 1.75 oz",
         "Simply Organic Chipotle Chile Powder, 2.89 oz", "McCormick Sweet Paprika, 2.12 oz"],
        "McCormick Chili Powder, 2.5 oz",
    ),
    (
        "Chicken Tikka Masala",
        ["boneless chicken thighs", "plain yogurt", "garam masala", "tomato puree", "heavy cream", "fresh ginger", "garlic", "ground cumin"],
        "heavy cream",
        ["Organic Valley Heavy Whipping Cream, 1 pint", "Land O'Lakes Half & Half, 1 quart",
         "Thai Kitchen Coconut Cream, 13.5 oz", "Daisy Sour Cream, 16 oz"],
        "Organic Valley Heavy Whipping Cream, 1 pint",
    ),
    (
        "Creamy Mushroom Risotto",
        ["Arborio rice", "cremini mushrooms", "vegetable broth", "dry white wine", "Parmesan cheese", "butter", "onion", "garlic"],
        "Arborio rice",
        ["Jasmine Rice, 2 lb bag", "Basmati Rice, 2 lb bag", "Long Grain White Rice, 5 lb bag", "Brown Rice, 2 lb bag"],
        None,
    ),
    (
        "Classic Caesar Salad",
        ["romaine lettuce", "Parmesan cheese", "croutons", "egg yolk", "anchovy fillets", "lemon juice", "garlic", "olive oil"],
        "anchovy fillets",
        ["Roland Anchovy Fillets in Olive Oil, 2 oz", "Bumble Bee Sardines in Water, 3.75 oz",
         "Ortiz Boquerones White Anchovies in Vinegar, 3.5 oz", "Amore Anchovy Paste, 1.6 oz"],
        "Roland Anchovy Fillets in Olive Oil, 2 oz",
    ),
    (
        "Shrimp Pad Thai",
        ["rice noodles", "shrimp", "eggs", "bean sprouts", "tamarind paste", "fish sauce", "peanuts", "lime"],
        "tamarind paste",
        ["Tamicon Tamarind Paste, 8 oz jar", "Thai Kitchen Fish Sauce, 6.76 oz",
         "Mae Ploy Sweet Chili Sauce, 16 oz", "Marukan Rice Vinegar, 12 oz"],
        "Tamicon Tamarind Paste, 8 oz jar",
    ),
    (
        "French Onion Soup",
        ["yellow onions", "beef broth", "dry sherry", "baguette", "Gruyere cheese", "butter", "thyme", "bay leaf"],
        "Gruyere cheese",
        ["Boar's Head Gruyere Cheese, Sliced, 6 oz", "Boar's Head Swiss Cheese, Sliced, 8 oz",
         "Great Value Mozzarella Cheese, Shredded, 8 oz", "Kraft Grated Parmesan Cheese, 5 oz"],
        "Boar's Head Gruyere Cheese, Sliced, 6 oz",
    ),
    (
        "Tom Yum Goong",
        ["shrimp", "lemongrass", "kaffir lime leaves", "galangal", "Thai chilies", "fish sauce", "lime juice", "mushrooms"],
        "galangal",
        ["Fresh Galangal Root, 4 oz", "Fresh Ginger Root, 4 oz", "Fresh Turmeric Root, 4 oz", "Fresh Lemongrass Stalks, 3 ct"],
        "Fresh Galangal Root, 4 oz",
    ),
]


def run_stage3():
    results = []
    for recipe_name, all_ingredients, ingredient, names, expected in STAGE3_CASES:
        batch = [{"ingredient": ingredient, "candidates": [{"name": n} for n in names]}]
        try:
            index = product_matcher.pick_best_matches(recipe_name, all_ingredients, batch)[0]
            got = names[index] if isinstance(index, int) else None
        except Exception as e:
            got = f"ERROR: {e}"
        passed = got == expected
        results.append((recipe_name, ingredient, names, expected, got, passed))

    print(f"\n[Stage 3] {sum(r[5] for r in results)}/{len(results)} cases matched expectations\n")
    for recipe_name, ingredient, names, expected, got, passed in results:
        status = PASS if passed else FAIL
        print(f"[{status}] {recipe_name!r} / ingredient={ingredient!r} over {names}")
        print(f"       expected: {expected}")
        print(f"       got:      {got}")
        print()


if __name__ == "__main__":
    run()
    run_stage3()
