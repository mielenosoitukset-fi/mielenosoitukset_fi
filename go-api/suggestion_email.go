package main

// Moderator notification mails for the two suggestion forms. Flask renders
// admin_org_suggestion_notification.html and admin_suggestion_notification.html
// (Jinja, autoescape) into email_queue; Go must enqueue the same rendered mail
// because the existing worker only sends finished bodies. The text below is
// those templates ported by hand; the parity test compares the output with
// Flask's, ignoring insignificant whitespace.

import (
	"strings"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

const suggestionMailHead = `<!DOCTYPE html>
<html lang="fi">
<head>
    <meta charset="UTF-8">
    <title>%SUBJECT%</title>
    <style>
        :root {
            color-scheme: light;
        }
        body {
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #f5f5fb;
            color: #1f2937;
        }
        .email-wrapper {
            width: 100%;
            padding: 32px 16px;
            box-sizing: border-box;
        }
        .email-card {
            max-width: 640px;
            margin: 0 auto;
            background-color: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 16px;
            box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
            padding: 32px;
        }
        .eyebrow {
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-size: 11px;
            color: #64748b;
            margin: 0 0 8px 0;
        }
        h1 {
            margin: 0 0 16px 0;
            font-size: 22px;
            color: #0f172a;
        }
        p {
            margin: 0 0 16px 0;
            line-height: 1.6;
            color: #1f2937;
        }
        .data-list {
            list-style: none;
            padding: 0;
            margin: 0 0 16px 0;
        }
        .data-list li {
            margin-bottom: 10px;
            padding: 10px 12px;
            background-color: #f8fafc;
            border-radius: 10px;
            border: 1px solid #e2e8f0;
        }
        .data-list strong {
            display: block;
            font-size: 13px;
            letter-spacing: 0.02em;
            color: #475569;
            text-transform: uppercase;
            margin-bottom: 4px;
        }
        .change-value {
            margin-top: 8px;
            font-size: 14px;
            line-height: 1.5;
        }
        .change-value span {
            display: block;
            font-size: 11px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #94a3b8;
            margin-bottom: 4px;
        }
        .change-value--new {
            border-left: 3px solid #22c55e;
            padding-left: 10px;
        }
        .change-value--original {
            border-left: 3px solid #94a3b8;
            padding-left: 10px;
            color: #475569;
        }
        .btn-group {
            margin: 12px 0 16px 0;
        }
        .btn {
            display: inline-block;
            padding: 11px 18px;
            font-weight: 600;
            border-radius: 999px;
            text-decoration: none;
            font-size: 14px;
            margin-right: 10px;
        }
        .btn-primary {
            background-color: #2563eb;
            color: #ffffff !important;
        }
        .btn-danger {
            background-color: #dc2626;
            color: #ffffff !important;
        }
        .btn-muted {
            background-color: #e2e8f0;
            color: #1f2937 !important;
        }
        .meta {
            margin-top: 24px;
            padding-top: 16px;
            border-top: 1px solid #e5e7eb;
            font-size: 13px;
            color: #475569;
        }
        .footer {
            text-align: center;
            color: #94a3b8;
            font-size: 12px;
            margin-top: 16px;
        }
        @media (max-width: 600px) {
            .email-card {
                padding: 24px;
            }
            .btn-group {
                display: flex;
                flex-direction: column;
            }
            .btn {
                margin-right: 0;
                margin-bottom: 10px;
                text-align: center;
            }
        }
    </style>
</head>
`

// jinjaEscape is markupsafe.escape.
var jinjaEscape = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", "'", "&#39;", "\"", "&#34;")

func suggestionMailPage(subject, eyebrow, heading, body string) string {
	return strings.Replace(suggestionMailHead, "%SUBJECT%", jinjaEscape.Replace(subject), 1) +
		`<body>
    <div class="email-wrapper">
        <div class="email-card">
            <p class="eyebrow">` + jinjaEscape.Replace(eyebrow) + `</p>
            <h1>` + jinjaEscape.Replace(heading) + `</h1>
` + body + `
        </div>
        <p class="footer">Mielenosoitukset.fi ylläpito</p>
    </div>
</body>
</html>`
}

// jinjaOutput renders {{ value }}: None prints "None", a missing key nothing.
func jinjaOutput(value any, present bool) string {
	if !present {
		return ""
	}
	return jinjaEscape.Replace(pyStr(value))
}

type orgMailField struct {
	Key   string
	Value any
}

// renderOrgSuggestionMail is admin_org_suggestion_notification.html.
func renderOrgSuggestionMail(orgName any, fields []orgMailField, reviewLink string, submittedAt time.Time, ip string, userAgent any) string {
	var items strings.Builder
	if len(fields) > 0 {
		for _, field := range fields {
			value := "—"
			if pyTruthy(field.Value) {
				value = jinjaEscape.Replace(pyStr(field.Value))
			}
			items.WriteString("<li><strong>" + jinjaEscape.Replace(field.Key) + "</strong> " + value + "</li>\n")
		}
	} else {
		items.WriteString("<li><strong>Ei kenttämuutoksia</strong> Käyttäjä jätti vain kommentin tai lisätiedon.</li>\n")
	}
	body := `<p>
    Käyttäjä on lähettänyt ehdotuksen organisaatiolle <strong>` + jinjaOutput(orgName, true) + `</strong>.
    Tarkista ehdotetut kentät ja hyväksy tai hylkää muutos ylläpitonäkymässä.
</p>

<h2>Ehdotetut muutokset</h2>
<ul class="data-list">
` + items.String() + `</ul>

<div class="btn-group">
    <a href="` + jinjaEscape.Replace(reviewLink) + `" class="btn btn-primary">Avaa ehdotuksen tarkastelu</a>
</div>

<div class="meta">
    <p><strong>Lähettäjän IP:</strong> ` + jinjaEscape.Replace(ip) + `</p>
    <p><strong>Selain:</strong> ` + jinjaOutput(userAgent, true) + `</p>
    <p><strong>Lähetetty:</strong> ` + submittedAt.UTC().Format("2006-01-02 15:04") + ` UTC</p>
</div>`
	return suggestionMailPage("Uusi organisaatiotietojen muokkauspyyntö", "Organisaatiotiedot", "Uusi muokkauspyyntö organisaatiolle", body)
}

// renderValue is the render_value macro of admin_email_macros.html (the
// mapping branch cannot occur: no suggestible field is a mapping).
func renderValue(value any) string {
	if list, ok := listItems(value); ok {
		return jinjaEscape.Replace(joinPy(list))
	}
	if !pyTruthy(value) {
		return "—"
	}
	return jinjaEscape.Replace(pyStr(value))
}

func listItems(value any) ([]any, bool) {
	switch typed := value.(type) {
	case []any:
		return typed, true
	case bson.A:
		return []any(typed), true
	case []string:
		out := make([]any, len(typed))
		for index, item := range typed {
			out[index] = item
		}
		return out, true
	}
	return nil, false
}

func joinPy(value any) string {
	list, _ := listItems(value)
	parts := make([]string, len(list))
	for index, item := range list {
		parts[index] = pyStr(item)
	}
	return strings.Join(parts, ", ")
}

type demoMailChange struct {
	Field    string
	Value    any
	Original any
	HasOrig  bool // original_values has the key and it is not None
}

type demoMailInput struct {
	Demo         func(key string) (any, bool)
	Changes      []demoMailChange
	Comment      string
	ReporterMail string
	IP           string
	UserAgent    any
	CreatedAt    time.Time
}

// renderDemoSuggestionMail is admin_suggestion_notification.html.
func renderDemoSuggestionMail(in demoMailInput) string {
	title, titlePresent := in.Demo("title")
	var body strings.Builder
	body.WriteString(`<p>
    Ylläpitoon saapui ehdotus, joka koskee mielenosoitusta <strong>` + jinjaOutput(title, titlePresent) + `</strong>.
    Tarkista alla olevat muutokset ja merkitse pyyntö käsitellyksi admin-paneelin ehdotusnäkymässä.
</p>

<h2>Mielenosoituksen tiedot</h2>
<ul class="data-list">
    <li>
        <strong>Otsikko</strong>
        ` + jinjaOutput(title, titlePresent) + `
    </li>
`)
	for _, row := range []struct{ key, label string }{{"date", "Päivämäärä"}, {"city", "Kaupunki"}, {"address", "Osoite"}} {
		if value, ok := in.Demo(row.key); ok && pyTruthy(value) {
			body.WriteString("    <li>\n        <strong>" + row.label + "</strong>\n        " + jinjaOutput(value, true) + "\n    </li>\n")
		}
	}
	body.WriteString("</ul>\n\n<h2>Ehdotetut muutokset</h2>\n")
	if len(in.Changes) > 0 {
		body.WriteString("<ul class=\"data-list\">\n")
		for _, change := range in.Changes {
			body.WriteString("    <li>\n        <strong>" + jinjaEscape.Replace(change.Field) + "</strong>\n        <div class=\"change-value change-value--new\">\n            <span>Ehdotettu arvo</span>\n            " + renderValue(change.Value) + "\n        </div>\n")
			if change.HasOrig {
				body.WriteString("        <div class=\"change-value change-value--original\">\n            <span>Nykyinen arvo</span>\n            " + renderValue(change.Original) + "\n        </div>\n")
			}
			body.WriteString("    </li>\n")
		}
		body.WriteString("</ul>\n")
	} else {
		body.WriteString("<p>Käyttäjä ei ehdottanut yksittäisiä kenttämuutoksia, mutta jätti kommentin tarkistettavaksi.</p>\n")
	}
	if in.Comment != "" {
		body.WriteString("\n<h2>Lisäkommentti</h2>\n<p>" + jinjaEscape.Replace(in.Comment) + "</p>\n")
	}
	body.WriteString("\n<div class=\"meta\">\n")
	if in.ReporterMail != "" {
		body.WriteString("    <p><strong>Ilmoittajan sähköposti:</strong> " + jinjaEscape.Replace(in.ReporterMail) + "</p>\n")
	}
	body.WriteString("    <p><strong>IP-osoite:</strong> " + jinjaEscape.Replace(in.IP) + "</p>\n")
	body.WriteString("    <p><strong>User-Agent:</strong> " + jinjaOutput(in.UserAgent, true) + "</p>\n")
	body.WriteString("    <p><strong>Vastaanotettu:</strong> " + in.CreatedAt.UTC().Format("02.01.2006 15:04") + " UTC</p>\n</div>")
	return suggestionMailPage("Uusi ehdotus: "+pyStrIfPresent(title, titlePresent), "Mielenosoituksen päivitys", "Uusi ehdotus mielenosoituksen päivittämiseksi", body.String())
}

func pyStrIfPresent(value any, present bool) string {
	if !present {
		return ""
	}
	return pyStr(value)
}
