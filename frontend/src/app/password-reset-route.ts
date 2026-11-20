// Password reset request page, GET /users/auth/password_reset_request. The form is plain
// HTML that posts to the unchanged Flask handler, which always answers with the same
// notice (no account enumeration), queues the mail and redirects to the login page.
export const passwordResetRequestPath = '/users/auth/password_reset_request';

export const passwordResetRequestCopy = {
  title: 'Unohtuiko salasanasi?',
  lead: 'Syötä sähköpostiosoitteesi, niin lähetämme sinulle salasanan palautusohjeet',
  emailLabel: 'Sähköpostiosoite',
  emailPlaceholder: 'nimi@esimerkki.fi',
  emailHint: 'Käytä mielenosoitukset.fi -palvelussa käyttämääsi sähköpostiosoitetta',
  submit: 'Lähetä palautuslinkki',
  remembered: 'Muistitko salasanasi?',
  backToLogin: 'Takaisin kirjautumiseen',
  footer: 'Saat sähköpostin muutaman minuutin sisällä, jos tilisi on olemassa',
};
