'use client';

import {
  ArrowRightOutlined,
  BarChartOutlined,
  CheckCircleOutlined,
  DownOutlined,
  GithubOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { useDataMode } from './data-mode';
import { LandingPreview } from './LandingPreview';
import styles from './landing.module.css';

const repository = 'https://github.com/ZAMYL/weiran-edu';
const tagline = 'AI 时代的编程能力与学习成效诊断平台';
const sections = [
  { id: 'hero', label: '平台概览' },
  { id: 'features', label: '核心流程' },
  { id: 'cta', label: '立即体验' },
] as const;
const highlights = [
  ['多维评估', '多维编程能力结构画像'],
  ['循证核验', '每项洞察均附样本统计依据'],
  ['群体分层', '精准识别班级薄弱环节'],
];
const workflow = [
  {
    icon: UploadOutlined,
    title: '1. 导入编程测评资料',
    description:
      '支持导入课程编程测验、实验评价与多维评分表，自动整理参评学生样本与各维度有效评分记录。',
    note: '自动校验样本完整性与有效配对记录',
  },
  {
    icon: BarChartOutlined,
    title: '2. 五维画像与群体差异探索',
    description:
      '从算法与数据结构、代码实现、问题建模、效率意识、解题策略五个维度刻画班级画像，支持双轴分布分析与条件筛选。',
    note: '精准定位共性短板与不同能力层级群体',
  },
  {
    icon: CheckCircleOutlined,
    title: '3. 证据核验与报告生成',
    description:
      '每条学情发现均附带统计样本量与相关性依据，支持教师逐条审核筛选并导出结构化教学分析报告。',
    note: '全链路数据溯源，支撑科学教学决策',
  },
];

export default function Home() {
  const router = useRouter();
  const { connected, openLogin, ready, testMode } = useDataMode();
  const enterAfterLogin = useRef(false);
  const [activeSection, setActiveSection] = useState<(typeof sections)[number]['id']>('hero');

  useEffect(() => {
    if (connected && enterAfterLogin.current) {
      enterAfterLogin.current = false;
      router.push('/insights');
    }
  }, [connected, router]);

  useEffect(() => {
    function updateSection() {
      const atBottom =
        window.scrollY > 0 &&
        window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 40;
      const section = sections.findLast(
        ({ id }) =>
          (document.getElementById(id)?.getBoundingClientRect().top ?? Infinity) <=
          window.innerHeight * 0.42,
      );
      setActiveSection(atBottom ? 'cta' : (section?.id ?? 'hero'));
    }
    updateSection();
    window.addEventListener('scroll', updateSection, { passive: true });
    window.addEventListener('resize', updateSection);
    return () => {
      window.removeEventListener('scroll', updateSection);
      window.removeEventListener('resize', updateSection);
    };
  }, []);

  function enterWorkspace() {
    if (connected || testMode) {
      router.push('/insights');
      return;
    }
    enterAfterLogin.current = true;
    openLogin(true);
  }

  return (
    <div className={styles.landing}>
      <nav className={styles.sectionNav} aria-label="页面快速导航">
        {sections.map(({ id, label }) => (
          <a key={id} href={`#${id}`} aria-current={activeSection === id ? 'location' : undefined}>
            {label}
          </a>
        ))}
        <a className={styles.navRepository} href={repository} target="_blank" rel="noreferrer">
          <GithubOutlined aria-hidden="true" /> GitHub
        </a>
      </nav>
      <main>
        <section id="hero" className={`${styles.container} ${styles.hero}`}>
          <div className={styles.heroGrid}>
            <div className={styles.heroCopy}>
              <div className={styles.brandLine}>
                <span className={styles.brand}>未然 EDU</span>
                <span className={styles.tagline}>{tagline}</span>
              </div>
              <h1>
                <span className={styles.titleLine}>让每一份测评数据，</span>
                <span className={styles.titleLine}>
                  都成为<span>有依据</span>的教学洞察
                </span>
              </h1>
              <p>上传编程测评资料，发现能力差异，获得可核验的群体画像和教学报告。</p>
              <div className={styles.actions}>
                <button
                  className={styles.action}
                  type="button"
                  disabled={!ready}
                  onClick={enterWorkspace}
                >
                  进入教师工作台 <ArrowRightOutlined aria-hidden="true" />
                </button>
                <a
                  className={styles.secondaryAction}
                  href={repository}
                  target="_blank"
                  rel="noreferrer"
                >
                  <GithubOutlined aria-hidden="true" /> 开源仓库
                </a>
              </div>
              <dl className={styles.highlights}>
                {highlights.map(([label, description]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{description}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <LandingPreview onEnter={enterWorkspace} ready={ready} />
          </div>
          <a className={styles.scrollCue} href="#features">
            向下滑动了解核心流程 <DownOutlined aria-hidden="true" />
          </a>
        </section>
        <section id="features" className={`${styles.container} ${styles.features}`}>
          <div className={styles.featuresIntro}>
            <span>核心流程</span>
            <h2>从原始测评资料到可核验的教学行动</h2>
            <p>围绕编程教学与课程评估场景构建，每一项诊断结论均可回溯至原始评分依据。</p>
          </div>
          <div className={styles.workflow}>
            {workflow.map(({ icon: Icon, title, description, note }) => (
              <article className={styles.workflowCard} key={title}>
                <span className={styles.workflowIcon}>
                  <Icon aria-hidden="true" />
                </span>
                <h3>{title}</h3>
                <p>{description}</p>
                <span className={styles.workflowNote}>{note}</span>
              </article>
            ))}
          </div>
        </section>
        <section id="cta" className={`${styles.container} ${styles.cta}`}>
          <div className={styles.ctaCard}>
            <div>
              <h2>开启数据驱动的编程教学诊断</h2>
              <p>
                进入未然 EDU 教师工作台，上传编程测评资料，快速生成可核验的群体画像与教学分析报告。
              </p>
            </div>
            <div className={styles.actions}>
              <button
                className={styles.action}
                type="button"
                disabled={!ready}
                onClick={enterWorkspace}
              >
                进入教师工作台 <ArrowRightOutlined aria-hidden="true" />
              </button>
              <a
                className={styles.secondaryAction}
                href={repository}
                target="_blank"
                rel="noreferrer"
              >
                <GithubOutlined aria-hidden="true" /> GitHub 仓库
              </a>
            </div>
          </div>
        </section>
      </main>
      <footer className={styles.footer}>
        <div className={styles.container}>
          <p>
            <strong className={styles.brand}>未然 EDU</strong> ｜ {tagline}
          </p>
          <p>
            <a href={repository} target="_blank" rel="noreferrer">
              <GithubOutlined aria-hidden="true" /> github.com/ZAMYL/weiran-edu
            </a>{' '}
            <span>© 2026 未然 EDU</span>
          </p>
        </div>
      </footer>
    </div>
  );
}
