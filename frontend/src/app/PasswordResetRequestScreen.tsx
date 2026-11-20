import type { AppConfig } from './config';
import { passwordResetRequestCopy as copy, passwordResetRequestPath } from './password-reset-route';
import './information/information.css';
import './auth-pages/auth-pages.css';

/** Plain form posting to Flask, so it works the same with and without JavaScript. */
export default function PasswordResetRequestScreen({ config }: { config: AppConfig }) {
  return <article className="public-page-shell information-page auth-page" aria-labelledby="reset-request-title">
    <header className="public-page-header"><div className="public-page-header__content">
      <h1 className="public-page-header__title" id="reset-request-title">{copy.title}</h1>
      <p className="public-page-header__description">{copy.lead}</p>
    </div></header>
    <section className="public-prose-card auth-card" aria-labelledby="reset-request-title">
      <form className="auth-form" action={passwordResetRequestPath} method="post">
        <label htmlFor="reset-email">{copy.emailLabel}</label>
        <input type="email" id="reset-email" name="email" required autoComplete="email" maxLength={254}
          placeholder={copy.emailPlaceholder} aria-describedby="reset-email-hint" />
        <p className="auth-hint" id="reset-email-hint">{copy.emailHint}</p>
        <div className="detail-actions">
          <button type="submit" className="detail-button detail-button--primary">{copy.submit}</button>
        </div>
      </form>
      <p className="auth-hint">{copy.footer}</p>
    </section>
    <section className="public-prose-card auth-card auth-card--secondary" aria-label={copy.remembered}>
      <p>{copy.remembered}</p>
      <div className="detail-actions"><a className="detail-button" href={config.urls.login}>{copy.backToLogin}</a></div>
    </section>
  </article>;
}
