package main

// Change notice for the general settings write (docs/account-pages-react.md, step 3b).
// settingsMail* is mielenosoitukset_fi/templates/emails/auth/settings_changed.html
// ported by hand, whitespace included; go-api/testdata/settings_parity.json holds
// Flask's real render and TestSettingsParityMail compares it byte for byte.

const settingsMailHead = `<!DOCTYPE html>
<html lang="fi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Käyttäjäasetusten muutokset</title>
    <style>
        body {
            background: #f4f4f4;
            color: #333333;
            margin: 0;
            padding: 0;
        }
        .container {
            width: 100%;
            max-width: 600px;
            padding: 20px;
            background: #ffffff;
            margin: 0 auto;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }
        .header {
            text-align: center;
            padding: 10px 0;
            background: #0033A0;
            color: #ffffff;
            border-radius: 8px 8px 0 0;
        }
        .content {
            margin: 20px;
        }
        .changes {
            padding-left: 1.25rem;
        }
        .changes li {
            margin-bottom: 0.75rem;
        }
        .value-label {
            color: #666666;
            font-size: 0.9em;
        }
        .footer {
            margin-top: 20px;
            text-align: center;
            font-size: 0.9em;
            color: #888888;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Käyttäjäasetuksia muutettiin</h1>
        </div>
        <div class="content">
            <p>Hei `

// settingsMailMid follows the greeting and ends where the for tag begins.
const settingsMailMid = `,</p>
            <p>Tilisi käyttäjäasetuksia on päivitetty. Muutetut kentät:</p>
            <ul class="changes">
                `

// settingsMailTail follows the endfor tag; Jinja drops the file's final newline.
const settingsMailTail = `
            </ul>
            <p>Jos et tehnyt tätä muutosta, ota välittömästi yhteyttä tukeemme.</p>
            <p>Ystävällisin terveisin,<br>Tiimimme</p>
        </div>
        <div class="footer">
            <p>Tämä on automaattinen viesti, älä vastaa tähän sähköpostiin.</p>
        </div>
    </div>
</body>
</html>`
