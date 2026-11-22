# Organisaation roolit

Rooli antaa oikeudet vain siihen organisaatioon, jossa se on. Omistajat ja adminit saavat ylläpito-oikeudet automaattisesti, ja ne päättyvät, kun rooli päättyy. Rooleja ei nimitetä erikseen.

## Jäsen
slug: member

- Kuuluu organisaation tiimiin ja näkee organisaation tiedot.
- Ei muokkaus- eikä hallintaoikeuksia.

## Admin
slug: admin

- Muokkaa organisaation tietoja.
- Kutsuu jäseniä ja hallinnoi jäseniä ja admineja.
- Luo ja muokkaa organisaation mielenosoituksia.
- Lukee organisaation tapauksia ja lisää niihin merkintöjä (ei muiden ihmisten yhteystietoja).

## Omistaja
slug: owner

- Kaikki mitä admin voi.
- Hallinnoi omistajia: vain omistaja antaa omistajan roolin tai poistaa omistajan. Viimeistä omistajaa ei voi poistaa eikä alentaa.
- Poistaa organisaation.

## Mielenosoitusten hyväksyminen

Ei kuulu rooleihin. Organisaation omistajat ja adminit päättävät organisaation omien mielenosoitusten tapaukset vain, jos Hallinto on myöntänyt organisaatiolle oikeuden hyväksyä omat mielenosoituksensa. Muuten mielenosoitukset hyväksyy moderaattori.
