// Sign-out confirmation, GET /users/auth/logout. Only the page moves to React. Prefetches and
// non-navigation fetches still reach Flask (Apache sends only navigations here), a signed-out
// visitor is sent to the login page, and the POST that ends the session, its token check and
// the Hallinto logout stay in the Flask handler.
export const logoutPath = '/users/auth/logout';

export const logoutCopy = {
  title: 'Haluatko kirjautua ulos?',
  lead: 'Kun kirjaudut ulos, istuntosi päättyy tässä selaimessa.',
  submit: 'Kirjaudu ulos',
  cancel: 'Peruuta',
};
