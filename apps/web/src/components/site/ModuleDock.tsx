import playlist from "@/site-data/playlist.json";
import { hasPlayableTracks } from "@/lib/public-ui";

interface ModuleEntry {
  key: string;
  title: string;
  description: string;
  icon: string;
  href: string;
}

const MODULES: ModuleEntry[] = [
  {
    key: "background",
    title: "自定义背景",
    description: "选择或上传背景图片,只保存在当前浏览器。",
    icon: "▣",
    href: "/#background",
  },
  {
    key: "player",
    title: "音乐播放器",
    description: "播放自有、授权或公版样例音乐,进度自动续播。",
    icon: "♪",
    href: "/#player",
  },
  {
    key: "console",
    title: "控制台",
    description: "文件、照片、邮件与知识库的私有入口。",
    icon: "✦",
    href: "/console",
  },
];

/** Drops the player card while the playlist is empty, so no card links to a missing dialog. */
const VISIBLE_MODULES = MODULES.filter(
  (entry) => entry.key !== "player" || hasPlayableTracks(playlist.tracks),
);

export default function ModuleDock() {
  return (
    <section aria-labelledby="modules-title" className="editorial-container module-dock">
      <div>
        <p className="section-kicker">03 · Passage</p>
        <h2 id="modules-title" className="section-title mt-4">选择一条航线</h2>
        <p className="mt-4 max-w-xs text-sm leading-7 text-text-dim">
          调整公开门户的观看方式，或进入只属于你的私人岛屿。
        </p>
      </div>
      <div className="module-dock__list">
        {VISIBLE_MODULES.map((m, index) => (
          <a
            key={m.key}
            href={m.href}
            className="module-link group focus-ring"
          >
            <span className="module-link__index">{String(index + 1).padStart(2, "0")}</span>
            <span className="module-link__title"><span className="module-link__icon" aria-hidden="true">{m.icon} </span>{m.title}</span>
            <span className="module-link__description">{m.description}</span>
            <span className="module-link__arrow" aria-hidden="true">→</span>
          </a>
        ))}
      </div>
    </section>
  );
}
