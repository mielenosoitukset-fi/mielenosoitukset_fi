import { guideCaptionBase, guideVideoBase, guideVideoGroups } from './guide-videos';

export function GuideVideos() {
  return (
    <section className="guide-section" id="videoohjeet">
      <div className="guide-section-heading">
        <div>
          <p className="guide-kicker">Videoohjeet</p>
          <h2>Katso, miten se tehdään</h2>
          <p>Videot on tehty harjoitusdatalla, joten niissä ei näy oikeita ihmisiä. Tekstitys on päällä. Kirjautumista ei tarvita videoiden katseluun.</p>
        </div>
      </div>
      {guideVideoGroups.map(group => (
        <div className="guide-video-group" id={`video-${group.key}`} key={group.key}>
          <h3>{group.title}</h3>
          <p className="guide-video-intro">{group.intro}</p>
          <div className="guide-video-grid">
            {group.videos.map(video => (
              <article className="guide-video-card" key={video.id}>
                <video controls preload="metadata" playsInline aria-label={video.title}>
                  <source src={`${guideVideoBase}/${video.id}.mp4`} type="video/mp4" />
                  <track default kind="captions" srcLang="fi" label="Suomi" src={`${guideCaptionBase}/${video.id}.fi.vtt`} />
                </video>
                <h4>{video.title}</h4>
                <p>{video.summary} <span className="guide-video-length">{video.minutes} min</span></p>
              </article>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
