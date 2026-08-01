"""A balanced, reviewable starter catalog for Rome, Florence, and Milan."""

from __future__ import annotations

from src.catalog import build_activity, load_curated_activities, save_activity
from src.models import Activity


# city, country, Wikidata ID, name, category, interests, walking, budget,
# duration, indoor, reservation, description
SUGGESTED_ACTIVITIES = [
    ("Rome", "Italy", "Q333906", "Capitoline Museums", "museum", ["art", "history", "sculpture"], "moderate", "moderate", 2.5, True, True, "Civic museums on Capitoline Hill with ancient sculpture, paintings, and Forum views."),
    ("Rome", "Italy", "Q486382", "Castel Sant'Angelo", "historic_site", ["history", "architecture", "photography"], "moderate", "moderate", 2.0, True, True, "A former mausoleum and fortress with museum rooms and rooftop city views."),
    ("Rome", "Italy", "Q189417", "Appian Way", "outdoor", ["ancient rome", "history", "archaeology", "nature"], "high", "free", 2.5, False, False, "An ancient Roman road lined with archaeological sites and open-air walking routes."),
    ("Rome", "Italy", "Q5786", "Arch of Constantine", "landmark", ["ancient rome", "history", "architecture", "photography"], "low", "free", 0.5, False, False, "A triumphal arch beside the Colosseum, built from Roman imperial reliefs."),
    ("Rome", "Italy", "Q207808", "Circus Maximus", "historic_site", ["ancient rome", "history", "archaeology", "outdoor"], "moderate", "free", 1.0, False, False, "The vast site of ancient Rome's chariot-racing stadium."),
    ("Rome", "Italy", "Q211295", "Trajan's Forum", "historic_site", ["ancient rome", "archaeology", "history", "architecture"], "moderate", "moderate", 1.5, False, False, "The final and largest of Rome's imperial forums, built under Emperor Trajan."),
    ("Florence", "Italy", "Q51252", "Uffizi Gallery", "museum", ["art", "culture", "history"], "moderate", "high", 3.0, True, True, "A major Renaissance art museum with works by Botticelli, Leonardo, and Michelangelo."),
    ("Florence", "Italy", "Q10855544", "Galleria dell'Accademia", "museum", ["art", "sculpture", "culture"], "low", "moderate", 1.5, True, True, "An art museum best known for Michelangelo's David and unfinished Prisoners."),
    ("Florence", "Italy", "Q388448", "Bargello National Museum", "museum", ["art", "sculpture", "history"], "moderate", "moderate", 2.0, True, True, "A former palace housing Renaissance sculpture and decorative arts."),
    ("Florence", "Italy", "Q888825", "Boboli Gardens", "park", ["nature", "relaxation", "photography", "history"], "high", "moderate", 2.0, False, True, "Historic landscaped gardens behind Palazzo Pitti with sculptures and city views."),
    ("Florence", "Italy", "Q191739", "Florence Cathedral", "landmark", ["architecture", "history", "religion", "culture"], "moderate", "free", 1.5, True, True, "Florence's cathedral, known for Brunelleschi's dome and Gothic-Renaissance architecture."),
    ("Florence", "Italy", "Q732511", "Florence Baptistery", "landmark", ["architecture", "history", "religion", "art"], "low", "moderate", 1.0, True, True, "An octagonal baptistery facing the cathedral, known for its bronze doors and mosaics."),
    ("Florence", "Italy", "Q51177", "Basilica of Santa Croce", "historic_site", ["architecture", "history", "religion", "art"], "moderate", "moderate", 1.5, True, False, "A Franciscan basilica containing major Florentine tombs and frescoes."),
    ("Florence", "Italy", "Q29286", "Palazzo Pitti", "museum", ["art", "history", "architecture", "culture"], "moderate", "moderate", 2.5, True, True, "A grand palace complex with royal apartments and art collections."),
    ("Florence", "Italy", "Q271928", "Palazzo Vecchio", "historic_site", ["history", "architecture", "art", "culture"], "moderate", "moderate", 1.5, True, True, "Florence's historic town hall with civic art, grand rooms, and a tower."),
    ("Florence", "Italy", "Q208633", "Ponte Vecchio", "landmark", ["architecture", "photography", "shopping", "history"], "low", "free", 0.75, False, False, "Florence's medieval bridge lined with small shops over the Arno."),
    ("Milan", "Italy", "Q18068", "Milan Cathedral", "landmark", ["architecture", "history", "religion", "photography"], "moderate", "moderate", 2.0, True, True, "A Gothic cathedral with an elaborate facade and rooftop terraces."),
    ("Milan", "Italy", "Q5471", "La Scala", "historic_site", ["music", "culture", "history", "architecture"], "low", "high", 2.0, True, True, "Milan's historic opera house, with performances and an adjoining museum."),
    ("Milan", "Italy", "Q150066", "Pinacoteca di Brera", "museum", ["art", "culture", "history"], "low", "moderate", 2.0, True, True, "A leading Italian art museum in the Brera district."),
    ("Milan", "Italy", "Q128910", "The Last Supper", "museum", ["art", "history", "religion", "culture"], "low", "moderate", 1.0, True, True, "Leonardo da Vinci's mural in the refectory of Santa Maria delle Grazie."),
    ("Milan", "Italy", "Q244952", "Santa Maria delle Grazie", "historic_site", ["architecture", "history", "religion", "art"], "low", "moderate", 1.0, True, True, "A Renaissance church and convent complex that houses The Last Supper."),
    ("Milan", "Italy", "Q23354", "Sforza Castle", "historic_site", ["history", "architecture", "art", "culture"], "moderate", "moderate", 2.0, True, False, "A Renaissance castle complex with civic museums and courtyards."),
    ("Milan", "Italy", "Q51112", "Galleria Vittorio Emanuele II", "landmark", ["architecture", "shopping", "photography", "culture"], "low", "free", 1.0, False, False, "A 19th-century glass-vaulted shopping gallery near the Duomo."),
    ("Milan", "Italy", "Q1071570", "Basilica of Sant'Ambrogio", "historic_site", ["architecture", "history", "religion", "culture"], "low", "free", 1.0, True, False, "An early Christian and Romanesque basilica dedicated to Milan's patron saint."),
    ("Milan", "Italy", "Q815611", "Biblioteca Ambrosiana", "museum", ["art", "history", "culture", "architecture"], "low", "moderate", 1.5, True, True, "A historic library and art collection founded by Cardinal Federico Borromeo."),
    ("Milan", "Italy", "Q920809", "Pirelli Tower", "landmark", ["architecture", "photography", "design"], "low", "free", 0.5, False, False, "A landmark modernist skyscraper designed by Gio Ponti and Pier Luigi Nervi."),
]


def suggested_activities() -> list[Activity]:
    """Build every suggested record through the shared Pydantic model."""

    activities = []
    for city, country, wikidata_id, name, category, interests, walking, budget, duration, indoor, reservation, description in SUGGESTED_ACTIVITIES:
        activities.append(build_activity(
            {"source_url": f"https://www.wikidata.org/wiki/{wikidata_id}"}, city, country,
            {"name": name, "category": category, "interests": interests, "walking_level": walking,
             "budget_level": budget, "duration_hours": duration, "indoor": indoor, "family_friendly": True,
             "accessibility_notes": "Check the official visitor information before visiting.",
             "reservation_required": reservation, "description": description,
             "source_url": f"https://www.wikidata.org/wiki/{wikidata_id}"},
        ))
    return activities


def seed_catalog() -> int:
    """Add suggestions not already curated and return the number added."""

    existing_ids = {activity.id for activity in load_curated_activities()}
    added = 0
    for activity in suggested_activities():
        if activity.id not in existing_ids:
            save_activity(activity)
            added += 1
    return added


if __name__ == "__main__":
    print(f"Added {seed_catalog()} curated activities.")
