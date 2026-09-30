"""A kitöltőszöveg-oldal (aaa2/entities/placeholder.py): nem hoz létre oldalhoz kötött
entitást, és a rajta álló említés demó-környezet."""
from aaa2.entities.placeholder import placeholder_share
from aaa2.entities.rules import run_rules
from aaa2.entities.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON, llm_entity

LOREM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
         "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
         "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.")
CICERO = ("Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium "
          "doloremque laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis "
          "et quasi architecto beatae vitae dicta sunt explicabo.")


def test_placeholder_share_counts_the_standard_filler_vocabulary():
    assert placeholder_share(LOREM) > 0.9 and placeholder_share(CICERO) > 0.9
    assert placeholder_share("A hőszivattyú a ház fűtését és a melegvizet adja.") == 0.0
    assert placeholder_share("Sed ut perspiciatis: a mérés a döntések alapja minden oldalon, "
                             "a riportok és a kampányok mögött.") < 0.5


def test_a_filler_page_makes_no_page_entity_and_its_mentions_are_demo():
    post = ld({"@type": "BlogPosting", "headline": "Sed ut perspiciatis",
               "author": {"@type": "Person", "name": "Finibus Bonorum"}})
    real = ld({"@type": "BlogPosting", "headline": "Hogyan mérj jól"})
    con = site({
        "/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
        "/blog/filler/": html("Sed ut perspiciatis · Pelda",
                              f"<main><h1>Sed ut perspiciatis</h1><p>{CICERO} Termékx.</p>"
                              f"<p>{LOREM}</p></main>", head=post),
        "/blog/cikk/": html("Hogyan mérj jól · Pelda",
                            "<main><h1>Hogyan mérj jól</h1><p>A mérés a döntések alapja, a "
                            "riportok és a kampányok mögött áll, minden héten újra.</p></main>",
                            head=real),
    })
    run_rules(con)
    llm_entity(con, "https://pelda.hu/blog/filler/", "Termékx", "Termékx", "product")
    run = run_site(con, clock=lambda: NOON)
    anchored = {name for (name,) in con.execute(
        "SELECT name FROM entities WHERE anchor_page_id IS NOT NULL").fetchall()}
    assert "Hogyan mérj jól" in anchored and "Sed ut perspiciatis" not in anchored
    assert run.placeholder_pages == ["https://pelda.hu/blog/filler/"]
    assert "Termékx" in run.demo
    assert "Finibus Bonorum" in run.demo                  # blokk nélküli schema-említés
