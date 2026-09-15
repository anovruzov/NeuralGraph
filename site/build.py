#!/usr/bin/env python3
"""Generates the static MSE site into site/ from the PAGES table below.

Page structure mirrors the forwarded "MSE ..." emails (Home, About Us, Services,
Project Services, Industries, Case Studies, Leadership, Methodology, Resources,
Digital Innovation, Contact). Every block marked TODO still needs the copy from
the matching email. Run:  python3 site/build.py
"""
from pathlib import Path
import html

OUT = Path(__file__).parent
BRAND = "MSE"
TAGLINE = "Engineering, project delivery and digital innovation"  # TODO: from "MSE website and brand"

NAV = [
    ("index.html", "Home"),
    ("about.html", "About"),
    ("services.html", "Services"),
    ("project-services.html", "Project Services"),
    ("industries.html", "Industries"),
    ("methodology.html", "Methodology"),
    ("case-studies.html", "Case Studies"),
    ("digital-innovation.html", "Digital Innovation"),
    ("leadership.html", "Leadership"),
    ("resources.html", "Resources"),
    ("contact.html", "Contact"),
]

def cards(items, cls="card"):
    out = []
    for title, body in items:
        out.append(f'<article class="{cls}"><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>')
    return '<div class="grid">' + "".join(out) + "</div>"

def hero(kicker, title, lead, cta=None):
    btn = f'<a class="btn" href="{cta[0]}">{html.escape(cta[1])}</a>' if cta else ""
    return f'''<section class="hero"><div class="wrap">
  <p class="kicker">{html.escape(kicker)}</p>
  <h1>{html.escape(title)}</h1>
  <p class="lead">{html.escape(lead)}</p>{btn}
</div></section>'''

def section(title, body_html, alt=False):
    return f'<section class="section{" alt" if alt else ""}"><div class="wrap"><h2>{html.escape(title)}</h2>{body_html}</div></section>'

PLACEHOLDER = "Copy for this block is in the forwarded email and will replace this text."

PAGES = {
 "index.html": ("Home", "MSE Home", [
    hero("MSE", TAGLINE, "MSE partners with clients to plan, deliver and improve complex engineering and capital projects.", ("contact.html", "Talk to us")),
    # TODO: from "Fwd: MSE Home"
    section("What we do", cards([
        ("Services", "Advisory, engineering and management services across the project lifecycle."),
        ("Project Services", "Planning, controls, procurement and delivery support for capital projects."),
        ("Digital Innovation", "Data, automation and digital tools that make projects more predictable."),
    ])),
    section("Why MSE", f'<p class="todo">{PLACEHOLDER}</p>', alt=True),
 ]),
 "about.html": ("About Us", "About MSE", [
    hero("About us", "Who we are", "TODO: opening statement from the “MSE About Us” email."),
    section("Our story", f'<p class="todo">{PLACEHOLDER}</p>'),
    section("Mission and values", cards([("Mission", PLACEHOLDER), ("Vision", PLACEHOLDER), ("Values", PLACEHOLDER)]), alt=True),
 ]),
 "services.html": ("Services", "MSE Services", [
    hero("Services", "How we help", "TODO: intro from the “Mse Services” email."),
    section("Service lines", cards([("Service 1", PLACEHOLDER), ("Service 2", PLACEHOLDER), ("Service 3", PLACEHOLDER), ("Service 4", PLACEHOLDER)])),
 ]),
 "project-services.html": ("Project Services", "MSE Project Services", [
    hero("Project services", "Delivery support across the project lifecycle", "TODO: intro from the “MSE project services” email."),
    section("Capabilities", cards([("Planning and scheduling", PLACEHOLDER), ("Cost and project controls", PLACEHOLDER), ("Procurement and contracts", PLACEHOLDER), ("Construction and commissioning", PLACEHOLDER)])),
 ]),
 "industries.html": ("Industries", "Industries MSE Serves", [
    hero("Industries", "Sectors we serve", "TODO: intro from the “Mse Industries” email."),
    section("Industries", cards([("Industry 1", PLACEHOLDER), ("Industry 2", PLACEHOLDER), ("Industry 3", PLACEHOLDER), ("Industry 4", PLACEHOLDER)])),
 ]),
 "methodology.html": ("Methodology", "MSE Methodology", [
    hero("Methodology", "How we work", "TODO: intro from the “MSE Methodology” email."),
    section("Our approach", '<ol class="steps">' + "".join(f'<li><strong>Step {i}</strong><span class="todo">{PLACEHOLDER}</span></li>' for i in range(1, 5)) + "</ol>"),
 ]),
 "case-studies.html": ("Case Studies", "MSE Case Studies", [
    hero("Case studies", "Selected work", "TODO: intro from the “MSE case Studies” email."),
    section("Projects", cards([("Case study 1", PLACEHOLDER), ("Case study 2", PLACEHOLDER), ("Case study 3", PLACEHOLDER)])),
 ]),
 "digital-innovation.html": ("Digital Innovation", "MSE Digital Innovation", [
    hero("Digital innovation", "Technology that improves project outcomes", "TODO: intro from the “MSE Digital Innovation” email."),
    section("Focus areas", cards([("Data and analytics", PLACEHOLDER), ("Automation", PLACEHOLDER), ("Digital delivery", PLACEHOLDER)])),
 ]),
 "leadership.html": ("Leadership", "MSE Leadership", [
    hero("Leadership", "Our team", "TODO: intro from the “MSE leadership” email."),
    section("Leadership team", cards([("Name, title", PLACEHOLDER), ("Name, title", PLACEHOLDER), ("Name, title", PLACEHOLDER)], cls="card person")),
 ]),
 "resources.html": ("Resources", "MSE Resources", [
    hero("Resources", "Insights and downloads", "TODO: intro from the “MSE Resources” email."),
    section("Latest", cards([("Resource 1", PLACEHOLDER), ("Resource 2", PLACEHOLDER), ("Resource 3", PLACEHOLDER)])),
 ]),
 "contact.html": ("Contact", "Contact MSE", [
    hero("Contact", "Get in touch", "TODO: intro from the “MSE Contact” email."),
    section("Contact details", '''<div class="contact">
  <div><h3>Office</h3><p class="todo">Address, phone and email from the “MSE Contact” email.</p></div>
  <form class="form" action="mailto:info@example.com" method="post" enctype="text/plain">
    <label>Name<input name="name" required></label>
    <label>Email<input name="email" type="email" required></label>
    <label>Message<textarea name="message" rows="5" required></textarea></label>
    <button class="btn" type="submit">Send</button>
  </form>
</div>'''),
 ]),
}

def layout(file, nav_label, title, body):
    links = ""
    for f, label in NAV:
        cur = ' aria-current="page"' if f == file else ""
        links += f'<a href="{f}"{cur}>{html.escape(label)}</a>'

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(BRAND)} – {html.escape(TAGLINE)}">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="styles.css">
</head>
<body>
<header class="site-header"><div class="wrap">
  <a class="logo" href="index.html">{html.escape(BRAND)}</a>
  <button class="menu" aria-label="Menu" aria-expanded="false" onclick="this.setAttribute('aria-expanded',this.getAttribute('aria-expanded')!=='true');document.body.classList.toggle('nav-open')">☰</button>
  <nav class="nav">{links}</nav>
</div></header>
<main>
{chr(10).join(body)}
</main>
<footer class="site-footer"><div class="wrap">
  <p>&copy; <span id="y"></span> {html.escape(BRAND)}. All rights reserved.</p>
  <nav class="foot">{links}</nav>
</div></footer>
<script>document.getElementById('y').textContent=new Date().getFullYear()</script>
</body>
</html>
'''

if __name__ == "__main__":
    for file, (nav_label, title, body) in PAGES.items():
        (OUT / file).write_text(layout(file, nav_label, title, body))
    print(f"built {len(PAGES)} pages into {OUT}")
