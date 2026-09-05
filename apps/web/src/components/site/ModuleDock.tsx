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

export default function ModuleDock() {
  return (
    <section aria-labelledby="modules-title" className="mx-auto w-full max-w-6xl px-4">
      <h2 id="modules-title" className="mb-4 text-xl font-semibold text-text">
        模块入口
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {MODULES.map((m) => (
          <a
            key={m.key}
            href={m.href}
            className="card card-hover group block p-4 focus-ring"
          >
            <div className="mb-2 flex items-center gap-2">
              <span aria-hidden="true" className="text-xl text-accent">{m.icon}</span>
              <span className="font-semibold text-text">{m.title}</span>
            </div>
            <p className="text-sm text-text-dim">{m.description}</p>
          </a>
        ))}
      </div>
    </section>
  );
}
