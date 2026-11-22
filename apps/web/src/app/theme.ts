import { theme as antTheme, type ThemeConfig } from 'antd';

export const tokens = antTheme.getDesignToken();

// Business layouts, SVG and stylesheet surfaces share the Ant palette, including portals.
function themeStyles(tokens: ReturnType<typeof antTheme.getDesignToken>, colorScheme: string) {
  const properties = {
    primary: tokens.colorPrimary,
    'primary-active': tokens.colorPrimaryActive,
    'primary-text': tokens.colorPrimaryText,
    'primary-bg': tokens.colorPrimaryBg,
    'primary-border': tokens.colorPrimaryBorder,
    canvas: tokens.colorBgLayout,
    surface: tokens.colorBgContainer,
    'surface-alter': tokens.colorFillAlter,
    border: tokens.colorBorderSecondary,
    'border-strong': tokens.colorBorder,
    text: tokens.colorText,
    muted: tokens.colorTextSecondary,
    subtle: tokens.colorTextTertiary,
    'fill-quaternary': tokens.colorFillQuaternary,
    'focus-ring': tokens.colorPrimaryBorder,
    success: tokens.colorSuccess,
    'success-bg': tokens.colorSuccessBg,
    warning: tokens.colorWarning,
    error: tokens.colorError,
    'error-bg': tokens.colorErrorBg,
    'accent-purple': tokens.purple6,
    'accent-purple-bg': tokens.purple1,
    radius: tokens.borderRadius + 'px',
    'heading-size': tokens.fontSizeHeading3 + 'px',
    'scrollbar-color': tokens.colorFill,
    'scrollbar-hover': tokens.colorTextQuaternary,
    gap: tokens.size + 'px',
    'space-xxs': tokens.sizeXXS + 'px',
    'space-xs': tokens.sizeXS + 'px',
    'space-sm': tokens.sizeSM + 'px',
    'space-md': tokens.size + 'px',
    'space-md-plus': tokens.sizeMD + 'px',
    'space-lg': tokens.sizeLG + 'px',
    'space-xl': tokens.sizeXL + 'px',
    'space-xxl': tokens.sizeXXL + 'px',
    'font-family': tokens.fontFamily,
  };
  return (
    `:root{color-scheme:${colorScheme};` +
    Object.entries(properties)
      .map(([name, value]) => '--' + name + ':' + value)
      .join(';') +
    '}'
  );
}

function createTheme(colorScheme: 'light' | 'dark') {
  const algorithm = colorScheme === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm;
  const palette = antTheme.getDesignToken({ algorithm });
  const config: ThemeConfig = {
    algorithm,
    cssVar: { key: 'weiran' },
    components: {
      Layout: { headerBg: palette.colorBgContainer },
      Tabs: { colorBgContainer: palette.colorBgLayout, horizontalMargin: '0' },
      Segmented: {
        itemSelectedBg: palette.colorPrimary,
        itemSelectedColor: palette.colorTextLightSolid,
      },
    },
  };
  return { config, styles: themeStyles(palette, colorScheme) };
}

export const themes = { light: createTheme('light'), dark: createTheme('dark') };
