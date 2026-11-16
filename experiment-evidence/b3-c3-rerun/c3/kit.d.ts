// Props of the components in scope in a page body. Generated from the ds types; read-only.
// Dom is the standard HTML/SVG attributes (className, style, onClick, aria-*, ...); a component
// passes them to the element it renders. Also in scope: React, useState, useMemo, useEffect,
// useRef, render(node), data (the data_path JSON, when given), chartColors: string[],
// Recharts (every Recharts 3 export).
type Dom = React.AllHTMLAttributes<HTMLElement> & React.SVGProps<SVGElement>;

declare function Card(props: {
} & Dom): JSX.Element;

declare function CardContent(props: {
  /** Allows you to replace the component's HTML element */
  render?: React.ReactElement<unknown, string | React.JSXElementConstructor<any>> | ComponentRenderFn<HTMLProps, {}> | undefined;
} & Dom): JSX.Element;

declare function CardBody(props: {
  children: React.ReactNode;
  className?: string | undefined;
} & Omit<Dom, "children" | "className">): JSX.Element;

declare function Text(props: {
  /** Render as a different element */
  as?: T | undefined;
  size?: 10 | 11 | 14 | 13 | 12 | 15 | 17;
  weight?: "medium" | "regular" | "semibold";
} & Omit<Dom, "as" | "size">): JSX.Element;

declare function Button(props: {
  variant?: "link" | "primary-accent" | "primary" | "destructive" | "destructive-muted" | "secondary" | "ghost" | "ghost-tight" | "accent-muted" | "warning" | "warning-muted";
  size?: "sm" | "md" | "lg";
  shape?: "square" | "rounded";
} & Omit<Dom, "size" | "shape">): JSX.Element;

declare function IconButton(props: {
  variant?: "primary-accent" | "primary" | "destructive" | "destructive-muted" | "secondary" | "ghost" | "ghost-tight" | "warning" | "warning-muted" | "ghost-quiet";
  /** Sizes below 24x24 are only acceptable when one of the WCAG 2.2 SC */
  size?: "sm" | "md" | "lg" | "xs";
  shape?: "circle" | "square" | "nobg";
} & Omit<Dom, "size" | "shape">): JSX.Element;

declare function Badge(props: {
  variant?: "destructive" | "warning" | "default" | "success" | "purple" | "orange" | "warning-solid" | "blue";
  size?: "sm" | "md";
  rounded?: boolean | null | undefined;
} & Omit<Dom, "size">): JSX.Element;

declare function Chip(props: {
  children?: React.ReactNode;
  icon?: React.ReactNode;
  className?: string | undefined;
  variant?: "filter" | "default" | "minimal";
  /** Pill (`rounded-full`) vs square (`rounded-[6px]`) corners. Defaults per */
  rounded?: boolean | undefined;
  as?: "a" | "button" | "span";
} & Omit<Dom, "children" | "className" | "as">): JSX.Element;

declare function MonoChip(props: {
  variant?: "code" | "link";
  children: ((string | number | bigint | boolean | React.ReactElement<unknown, string | React.JSXElementConstructor<any>> | Iterable<React.ReactNode> | React.ReactPortal | Promise<AwaitedReactNode>) & (string | number | bigint | boolean | React.ReactElement<unknown, string | React.JSXElementConstructor<any>> | Iterable<React.ReactNode> | React.ReactPortal | Promise<AwaitedReactNode> | Iterable<ReactI18NextChildren>)) | null | undefined;
  className?: string | undefined;
  as?: "code" | "span";
} & Omit<Dom, "children" | "className" | "as">): JSX.Element;

declare function ProgressBar(props: {
  "aria-label"?: string | undefined;
  "aria-labelledby"?: string | undefined;
  /** A number between 0 and 100 representing the completion percentage. */
  value: number;
} & Omit<Dom, "aria-label" | "aria-labelledby" | "value">): JSX.Element;

declare function BannerCard(props: {
  /** Color variant controlling the left highlight bar and icon color */
  variant: "warning" | "error" | "info";
  /** Icon displayed next to the title */
  icon?: React.ReactNode;
  /** Bold title text */
  title?: React.ReactNode;
  /** Optional description text below the title */
  children?: React.ReactNode;
  /** Optional action buttons rendered below the description on narrow cards, and to the right on wider cards */
  actions?: React.ReactNode;
  /** Always render title and actions in a single horizontal row, truncating text instead of stacking */
  inline?: boolean | undefined;
  /** Block content (e.g. illustrations) rendered below the description, above stacked actions */
  media?: React.ReactNode;
  /** Always render actions on their own row below the text, right-aligned, regardless of card width */
  stackedActions?: boolean | undefined;
  /** Additional CSS classes */
  className?: string | undefined;
} & Omit<Dom, "title" | "children" | "media" | "className">): JSX.Element;

declare function CodeBlock(props: {
  /** Additional class names */
  className?: string | undefined;
  /** The code string to display */
  code: string;
} & Omit<Dom, "className">): JSX.Element;

declare function Blockquote(props: {
} & Dom): JSX.Element;

declare function ActionsToolbar(props: {
  /** `"floating"` (default): absolutely positioned overlay; hosts position it */
  placement?: "inline" | "floating";
} & Dom): JSX.Element;

declare function ActionsToolbarButton(props: {
  disabled?: boolean | undefined;
  /** Tooltip label. When provided, wraps the button in a Tooltip. */
  tooltip?: string | undefined;
  /** Render a vertical separator before this action. */
  separatorBefore?: boolean | undefined;
} & Omit<Dom, "disabled">): JSX.Element;

declare function CopyIconButton(props: {
  value: string | (() => string);
  tooltip?: string | undefined;
} & Omit<Dom, "value">): JSX.Element;

declare function ExternalLink(props: {
  /** Text to show before the link (e.g., "@") */
  prefix?: string | undefined;
  children: React.ReactNode;
} & Omit<Dom, "prefix" | "children">): JSX.Element;

declare function CheckboxStatus(props: {
  /** Whether the component should ignore user interaction. */
  disabled?: boolean | undefined;
  size?: "sm" | "md" | "xs";
  /** Whether the checkbox is currently ticked. */
  checked?: boolean | undefined;
  round?: boolean | null | undefined;
  /** Whether the checkbox is in a mixed state: neither ticked, nor unticked. */
  indeterminate?: boolean | undefined;
  "aria-label": string;
  "aria-hidden"?: boolean | undefined;
} & Omit<Dom, "disabled" | "size" | "checked" | "aria-label" | "aria-hidden">): JSX.Element;

declare function ColorDot(props: {
  /** CSS color. A caller whose color is a `bg-*` class passes it in `className`. */
  color?: string | null | undefined;
  /** Ring with no fill, for an entry that stands for "none". */
  hollow?: boolean | undefined;
  className?: string | undefined;
} & Omit<Dom, "color" | "className">): JSX.Element;

declare function ArtifactLinkChip(props: {
  label: string;
  kind: "image" | "file" | "recording" | "pr" | "preview";
  /** The PR's state, for `kind="pr"`; ignored for other kinds. */
  prState?: "open" | "queued" | "draft" | "merged" | "closed";
  /** The site, for `kind="preview"`: its favicon stands in for the square */
  host?: string | undefined;
} & Omit<Dom, "label" | "kind">): JSX.Element;

declare function ScrollArea(props: {
  mask?: boolean | undefined;
  orientation?: "horizontal" | "vertical" | "both";
  viewportClassName?: string | undefined;
} & Omit<Dom, "mask" | "orientation">): JSX.Element;

declare function ScrollAreaContent(props: {
  /** Allows you to replace the component's HTML element */
  render?: React.ReactElement<unknown, string | React.JSXElementConstructor<any>> | ComponentRenderFn<HTMLProps, ScrollAreaContentState> | undefined;
} & Dom): JSX.Element;

declare function Input(props: {
  /** The value of the input. Use when controlled. */
  value?: string | number | readonly string[] | undefined;
  /** Callback fired when the `value` changes. Use when controlled. */
  onValueChange?: ((value: string) => void) | undefined;
  /** The default value of the input. Use when uncontrolled. */
  defaultValue?: string | number | readonly string[] | undefined;
  /** `ghost` renders chromeless at rest for read-mostly fields (value shown among non-editable text, editing secondary); never the default for input-first fields. */
  variant?: "form" | "ghost" | "default";
} & Omit<Dom, "value" | "defaultValue">): JSX.Element;

declare function Switch(props: {
  /** Whether the switch is initially active. */
  defaultChecked?: boolean | undefined;
  size?: "sm" | "md" | "xs";
  /** Whether the switch is currently active. */
  checked?: boolean | undefined;
  /** Event handler called when the switch is activated or deactivated. */
  onCheckedChange?: ((checked: boolean) => void) | undefined;
} & Omit<Dom, "defaultChecked" | "size" | "checked">): JSX.Element;

declare function SimpleCombobox(props: {
  /** The currently selected value; null selects nothing (shows the placeholder) */
  value?: string | null | undefined;
  /** Callback when the value changes */
  onValueChange?: ((value: string | null) => void) | undefined;
  /** Whether the combobox is disabled */
  disabled?: boolean | undefined;
  /** The list of options to render */
  options: SimpleComboboxOption[];
  /** Placeholder text when no value is selected */
  placeholder?: string | undefined;
  /** Placeholder text for the search input */
  searchPlaceholder?: string | undefined;
  /** Message when no results match the search */
  emptyMessage?: string | undefined;
} & Omit<Dom, "value" | "disabled" | "placeholder">): JSX.Element;

declare function Menu(props: {
  /** Whether the menu is currently open. */
  open?: boolean | undefined;
  /** Whether the menu is initially open. */
  defaultOpen?: boolean | undefined;
  /** Event handler called when the menu is opened or closed. */
  onOpenChange?: ((open: boolean) => void) | undefined;
} & Omit<Dom, "open">): JSX.Element;

declare function MenuTrigger(props: {
  /** Allows you to replace the component's HTML element */
  render?: React.ReactElement<unknown, string | React.JSXElementConstructor<any>> | ComponentRenderFn<HTMLProps, MenuTriggerState> | undefined;
  /** Whether the component should ignore user interaction. */
  disabled?: boolean | undefined;
} & Omit<Dom, "disabled">): JSX.Element;

declare function MenuContent(props: {
  children: React.ReactNode;
  align?: "start" | "end" | "center";
  /** Preferred side of the trigger (default: "bottom" for root menus, "right" for submenus) */
  side?: "left" | "right" | "top" | "bottom";
} & Omit<Dom, "children">): JSX.Element;

declare function MenuItem(props: {
  /** Whether the component should ignore user interaction. */
  disabled?: boolean | undefined;
  /** Visual style variant. Use "destructive" for dangerous actions like delete. */
  variant?: "destructive" | "default";
  /** Whether to close the menu when the item is clicked. */
  closeOnClick?: boolean | undefined;
} & Omit<Dom, "disabled">): JSX.Element;

declare function MenuSeparator(props: {
} & Dom): JSX.Element;

declare function MenuItemIcon(props: {
  className?: string | undefined;
  children: React.ReactNode;
} & Omit<Dom, "className" | "children">): JSX.Element;

declare function MenuItemText(props: {
  children: React.ReactNode;
  className?: string | undefined;
} & Omit<Dom, "children" | "className">): JSX.Element;

declare function MenuItemTooltip(props: {
  content: React.ReactNode;
  children: React.ReactElement<unknown, string | React.JSXElementConstructor<any>>;
  side?: "left" | "right" | "top" | "bottom";
  sideOffset?: number | undefined;
  align?: "start" | "end" | "center";
  alignOffset?: number | undefined;
  collisionAvoidance?: CollisionAvoidance | undefined;
  /** Drive the tooltip from hover only, ignoring focus. Base UI closes a */
  hoverOnly?: boolean | undefined;
} & Omit<Dom, "content" | "children">): JSX.Element;

declare function MenuGroup(props: {
  children: React.ReactNode;
  className?: string | undefined;
} & Omit<Dom, "children" | "className">): JSX.Element;

declare function MenuGroupLabel(props: {
  children: React.ReactNode;
} & Omit<Dom, "children">): JSX.Element;

declare function MenuCheckboxItem(props: {
  children: React.ReactNode;
  className?: string | undefined;
  checked?: boolean | undefined;
  onCheckedChange?: ((checked: boolean) => void) | undefined;
  disabled?: boolean | undefined;
  closeOnClick?: boolean | undefined;
  /** Indicator style: "check" shows a checkmark (default), "switch" shows a toggle switch, "checkbox" shows a checkbox square */
  indicator?: "switch" | "checkbox" | "check";
  /** Which side the indicator renders on; defaults to left for checkbox */
  checkSide?: "left" | "right";
} & Omit<Dom, "children" | "className" | "checked" | "disabled">): JSX.Element;

declare function MenuRadioGroup(props: {
  children: React.ReactNode;
  value?: string | undefined;
  onValueChange?: ((value: string) => void) | undefined;
  className?: string | undefined;
  /** Which side the checkmark renders on for every item in the group. Defaults */
  checkSide?: "left" | "right";
} & Omit<Dom, "children" | "value" | "className">): JSX.Element;

declare function MenuRadioItem(props: {
  children: React.ReactNode;
  value: string;
  className?: string | undefined;
  disabled?: boolean | undefined;
  closeOnClick?: boolean | undefined;
  trailing?: React.ReactNode;
} & Omit<Dom, "children" | "value" | "className" | "disabled">): JSX.Element;

declare function MenuRadioTabs(props: {
  children: React.ReactNode;
  value?: string | undefined;
  onValueChange?: ((value: string) => void) | undefined;
  className?: string | undefined;
} & Omit<Dom, "children" | "value" | "className">): JSX.Element;

declare function MenuRadioTab(props: {
  children: React.ReactNode;
  value: string;
  className?: string | undefined;
  disabled?: boolean | undefined;
} & Omit<Dom, "children" | "value" | "className" | "disabled">): JSX.Element;

declare function Submenu(props: {
  /** Whether the menu is currently open. */
  open?: boolean | undefined;
  /** Whether the menu is initially open. */
  defaultOpen?: boolean | undefined;
  /** The event details are absent when the submenu closes itself, e.g. the */
  onOpenChange?: ((open: boolean, eventDetails?: Parameters<NonNullable<((open: boolean) => void) | undefined>>[1]) => void) | undefined;
} & Omit<Dom, "open">): JSX.Element;

declare function SubmenuTrigger(props: {
  children: React.ReactNode;
  /** Currently selected value, shown right-aligned in secondary text. */
  value?: React.ReactNode;
  /** Disable the trigger so the submenu cannot be opened. */
  disabled?: boolean | undefined;
  /** Whether hovering the trigger opens the submenu (default true). */
  openOnHover?: boolean | undefined;
} & Omit<Dom, "children" | "value" | "disabled">): JSX.Element;

declare function SimpleSelect(props: {
  /** The currently selected value; pass `null` for a controlled empty selection */
  value?: string | null | undefined;
  onValueChange?: ((value: string) => void) | undefined;
  /** Whether the select is disabled */
  disabled?: boolean | undefined;
  /** The list of options to render */
  options: SimpleSelectOption[];
  /** Placeholder text when no value is selected */
  placeholder?: string | undefined;
} & Omit<Dom, "value" | "disabled" | "placeholder">): JSX.Element;

declare function Tooltip(props: {
  align?: "start" | "end" | "center";
  content: React.ReactNode;
  side?: "left" | "right" | "top" | "bottom";
  delayDuration?: number | undefined;
} & Omit<Dom, "content">): JSX.Element;

declare function AvatarRoot(props: {
} & Dom): JSX.Element;

declare function AvatarImage(props: {
} & Dom): JSX.Element;

declare function AvatarFallback(props: {
} & Dom): JSX.Element;

declare function TabsRoot(props: {
  /** The value of the currently active `Tab`. Use when the component is controlled. */
  value?: any;
  /** Callback invoked when new value is being set. */
  onValueChange?: ((value: TabsTab.Value) => void) | undefined;
  /** The default value. Use when the component is not controlled. */
  defaultValue?: any;
} & Omit<Dom, "value" | "defaultValue">): JSX.Element;

declare function TabsList(props: {
  /** Pill-shaped tabs with fully rounded corners. */
  rounded?: boolean | undefined;
} & Dom): JSX.Element;

declare function Tab(props: {
  /** The value of the Tab. */
  value: any;
} & Omit<Dom, "value">): JSX.Element;

declare function TabsPanel(props: {
  /** The value of the TabPanel. It will be shown when the Tab with the corresponding value is active. */
  value: any;
  /** Whether to keep the HTML element in the DOM while the panel is hidden. */
  keepMounted?: boolean | undefined;
} & Omit<Dom, "value">): JSX.Element;

declare function CollapsibleRoot(props: {
  /** Whether the collapsible panel is currently open. */
  open?: boolean | undefined;
  /** Whether the collapsible panel is initially open. */
  defaultOpen?: boolean | undefined;
  /** Event handler called when the panel is opened or closed. */
  onOpenChange?: ((open: boolean) => void) | undefined;
} & Omit<Dom, "open">): JSX.Element;

declare function CollapsibleTrigger(props: {
  /** Show caret icon (default: true) */
  showCaret?: boolean | undefined;
  /** Position caret left or right of children (default: "right") */
  caretPosition?: "left" | "right";
} & Dom): JSX.Element;

declare function CollapsiblePanel(props: {
  /** Whether to keep the element in the DOM while the panel is hidden. */
  keepMounted?: boolean | undefined;
} & Dom): JSX.Element;

declare function SegmentedControl(props: {
  value: string;
  onValueChange?: ((value: string) => void) | undefined;
  /** Whether the toggle group should ignore user interaction. */
  disabled?: boolean | undefined;
  /** `rectangle` uses subtle rounded corners, `rounded` is fully pill-shaped. */
  variant?: "rounded" | "rectangle";
  /** Item height and padding. */
  size?: "small" | "medium" | "large";
} & Omit<Dom, "value" | "disabled" | "size">): JSX.Element;

declare function SegmentedControlItem(props: {
  /** A unique string that identifies the toggle when used */
  value?: string | undefined;
} & Omit<Dom, "value">): JSX.Element;

declare function ChartContainer(props: {
  config: ChartConfig;
  children: React.ReactElement<unknown, string | React.JSXElementConstructor<any>>;
} & Omit<Dom, "children">): JSX.Element;

declare function ChartLegend(props: {
  /** Horizontal alignment of the whole Legend container: */
  align?: "center" | "left" | "right";
  /** The alignment of the whole Legend container: */
  verticalAlign?: "top" | "bottom" | "middle";
  /** The layout of legend items inside the legend container. */
  layout?: "horizontal" | "vertical";
} & Dom): JSX.Element;

declare function ChartLegendContent(props: {
  payload?: readonly LegendPayload[] | undefined;
  className?: string | undefined;
} & Omit<Dom, "className">): JSX.Element;

declare function ChartTooltip(props: {
  /** Function to customize the value in the tooltip. */
  formatter?: (Formatter<ValueType, NameType> & ((value: ValueType, name: NameType, item: TooltipPayloadEntry, index: number, payload: TooltipPayload) => React.ReactNode | [React.ReactNode, React.ReactNode])) | undefined;
  /** The formatter function of label in tooltip. */
  labelFormatter?: (((label: React.ReactNode, payload: readonly Payload<ValueType, NameType>[]) => React.ReactNode) & ((label: any, payload: TooltipPayload) => React.ReactNode)) | undefined;
  /** Defines whether the tooltip is reacting to the current data point, */
  shared?: boolean | undefined;
} & Dom): JSX.Element;

declare function ChartTooltipContent(props: {
  active?: boolean | undefined;
  className?: string | undefined;
  label?: string | number | undefined;
  payload?: TooltipPayload | undefined;
  hideLabel?: boolean | undefined;
} & Omit<Dom, "className" | "label">): JSX.Element;

declare function ChartGrid(props: {
  /** If set false, no horizontal grid lines will be drawn. */
  horizontal?: GridLineType | undefined;
  /** If set false, no vertical grid lines will be drawn. */
  vertical?: GridLineType | undefined;
} & Dom): JSX.Element;

declare function ChartXAxis(props: {
  /** Defines a single label for the whole axis. */
  label?: ImplicitLabelType | undefined;
  /** If set true, the axis do not display in the chart. */
  hide?: boolean | undefined;
  /** The type of axis. */
  type?: "number" | "auto" | "category";
  /** The data that you provide via the `data` prop is an array of objects. */
  dataKey?: string | number | ((obj: unknown) => unknown) | undefined;
  /** Unique ID that represents this XAxis. */
  xAxisId?: string | number | undefined;
  /** Specify the domain of axis when the axis is a number axis. */
  domain?: AxisDomain | undefined;
  /** Ticks can be any type when the axis is the type of category */
  ticks?: readonly AxisTick[] | undefined;
  /** The count of axis ticks. Not used if 'type' is 'category'. */
  tickCount?: number | undefined;
  /** The formatter function of tick. */
  tickFormatter?: ((value: any, index: number) => string) | undefined;
  /** If set 0, all the ticks will be shown. If set preserveStart", "preserveEnd" or "preserveStartEnd", the ticks which is to be shown or hidden will be calculated automatically. */
  interval?: AxisInterval | undefined;
  /** Tick text rotation angle in degrees. */
  angle?: number | undefined;
  /** The unit of data. This option will be used in tooltip. */
  unit?: string | undefined;
  /** Allow the ticks of axis to be decimals or not. */
  allowDecimals?: boolean | undefined;
} & Omit<Dom, "label" | "type">): JSX.Element;

declare function ChartYAxis(props: {
  /** What the axis measures, the unit or quantity, written along it. */
  title?: string | undefined;
  /** If set true, the axis do not display in the chart. */
  hide?: boolean | undefined;
  /** The type of axis. */
  type?: "number" | "auto" | "category";
  /** The data that you provide via the `data` prop is an array of objects. */
  dataKey?: string | number | ((obj: unknown) => unknown) | undefined;
  /** Unique ID that represents this YAxis. */
  yAxisId?: string | number | undefined;
  /** Specify the domain of axis when the axis is a number axis. */
  domain?: AxisDomain | undefined;
  /** Ticks can be any type when the axis is the type of category */
  ticks?: readonly AxisTick[] | undefined;
  /** The count of axis ticks. Not used if 'type' is 'category'. */
  tickCount?: number | undefined;
  /** The formatter function of tick. */
  tickFormatter?: ((value: any, index: number) => string) | undefined;
  /** If set 0, all the ticks will be shown. If set "preserveStart", "preserveEnd" or "preserveStartEnd", */
  interval?: AxisInterval | undefined;
  /** Tick text rotation angle in degrees. */
  angle?: number | undefined;
  /** The unit of data. This option will be used in tooltip. */
  unit?: string | undefined;
  /** Allow the ticks of axis to be decimals or not. */
  allowDecimals?: boolean | undefined;
} & Omit<Dom, "title" | "type">): JSX.Element;

declare function ChartBar(props: {
  fill?: string | undefined;
  /** Renders one label for each bar. Options: */
  label?: ImplicitLabelListType | undefined;
  /** Hides the whole graphical element when true. */
  hide?: boolean | undefined;
  /** The name of data. */
  name?: string | number | undefined;
  /** The radius of corners. */
  radius?: RectRadius | undefined;
  /** The data that you provide via the `data` prop is an array of objects. */
  dataKey?: string | number | ((obj: unknown) => unknown) | undefined;
  /** The id of XAxis which is corresponding to the data. Required when there are multiple XAxes. */
  xAxisId?: AxisId | undefined;
  /** The id of YAxis which is corresponding to the data. Required when there are multiple YAxes. */
  yAxisId?: AxisId | undefined;
  /** When two Bars have the same axisId and same stackId, then the two Bars are stacked in the chart. */
  stackId?: StackId | undefined;
  /** The width or height of each bar. If the barSize is not specified, the size of bar will be calculated by the barCategoryGap, barGap and the quantity of bar groups. */
  barSize?: string | number | undefined;
} & Omit<Dom, "fill" | "label" | "name" | "radius">): JSX.Element;

declare function ChartLine(props: {
  /** Renders one label for each data point. Options: */
  label?: ImplicitLabelListType | undefined;
  /** Hides the whole graphical element when true. */
  hide?: boolean | undefined;
  /** The name of data. */
  name?: string | undefined;
  /** The interpolation type of curve. Allows custom interpolation function. */
  type?: CurveType | undefined;
  /** The stroke color. If `"none"`, no line will be drawn. */
  stroke?: string | undefined;
  /** The data that you provide via the `data` prop is an array of objects. */
  dataKey?: string | number | ((obj: any) => any) | undefined;
  /** The id of YAxis which is corresponding to the data. Required when there are multiple YAxes. */
  yAxisId?: AxisId | undefined;
  /** Renders a circle element at each data point. Options: */
  dot?: DotType | undefined;
  /** Whether to connect the curve across null points. */
  connectNulls?: boolean | undefined;
} & Omit<Dom, "label" | "name" | "type" | "stroke">): JSX.Element;

declare function ChartArea(props: {
  fill?: string | undefined;
  /** Renders one label for each data point. Options: */
  label?: ImplicitLabelListType | undefined;
  /** Hides the whole graphical element when true. */
  hide?: boolean | undefined;
  /** The name of data. */
  name?: string | undefined;
  /** The interpolation type of curve. Allows custom interpolation function. */
  type?: CurveType | undefined;
  /** The stroke color. If "none", no line will be drawn. */
  stroke?: string | undefined;
  /** The data that you provide via the `data` prop is an array of objects. */
  dataKey: string | number | ((obj: unknown) => unknown);
  /** The id of YAxis which is corresponding to the data. Required when there are multiple YAxes. */
  yAxisId?: AxisId | undefined;
  /** When two Areas have the same axisId and same stackId, then the two Areas are stacked in the chart. */
  stackId?: StackId | undefined;
  /** Renders a circle element at each data point. Options: */
  dot?: DotType | undefined;
  /** Whether to connect the curve across null points. */
  connectNulls?: boolean | undefined;
} & Omit<Dom, "fill" | "label" | "name" | "type" | "stroke">): JSX.Element;

declare function ChartPie(props: {
  /** The source data. Each element should be an object. */
  data?: ChartData<any> | undefined;
  /** Renders one label for each pie sector. Options: */
  label?: PieLabel | undefined;
  /** The data that you provide via the `data` prop is an array of objects. */
  dataKey?: string | number | ((obj: any) => any) | undefined;
  /** Name represents each sector in the tooltip, and legend. */
  nameKey?: string | number | ((obj: any) => any) | undefined;
  /** The inner radius of the sectors. */
  innerRadius?: string | number | undefined;
  /** The outer radius of the sectors. */
  outerRadius?: string | number | ((dataPoint: any) => number | string) | undefined;
  /** Angle in degrees from which the chart should start. */
  startAngle?: number | undefined;
  /** Angle, in degrees, at which the chart should end. */
  endAngle?: number | undefined;
  /** The angle between two sectors. */
  paddingAngle?: number | undefined;
  cornerRadius?: string | number | undefined;
  /** If false set, label lines will not be drawn. If true set, label lines will be drawn which have the props calculated internally. */
  labelLine?: PieLabelLine | undefined;
} & Omit<Dom, "data" | "label">): JSX.Element;

declare function BarChart(props: {
  /** The source data. Each element should be an object. */
  data?: ChartData<unknown> | undefined;
  /** The layout of chart defines the orientation of axes, graphical items, and tooltip. */
  layout?: "horizontal" | "vertical";
  /** Empty space around the container. */
  margin?: Partial<Margin> | undefined;
  /** The gap between two bar categories, which can be a percent value or a fixed value. */
  barCategoryGap?: string | number | undefined;
  /** The gap between two bars in the same category. */
  barGap?: string | number | undefined;
  /** The type of offset function used to generate the lower and upper values in the series array. */
  stackOffset?: "none" | "sign" | "expand" | "wiggle" | "silhouette" | "positive";
} & Omit<Dom, "data">): JSX.Element;

declare function LineChart(props: {
  /** The source data. Each element should be an object. */
  data?: ChartData<unknown> | undefined;
  /** Empty space around the container. */
  margin?: Partial<Margin> | undefined;
} & Omit<Dom, "data">): JSX.Element;

declare function AreaChart(props: {
  /** The source data. Each element should be an object. */
  data?: ChartData<unknown> | undefined;
  /** Empty space around the container. */
  margin?: Partial<Margin> | undefined;
  /** The type of offset function used to generate the lower and upper values in the series array. */
  stackOffset?: "none" | "sign" | "expand" | "wiggle" | "silhouette" | "positive";
} & Omit<Dom, "data">): JSX.Element;

declare function PieChart(props: {
  /** Empty space around the container. */
  margin?: Partial<Margin> | undefined;
} & Dom): JSX.Element;

declare function ScatterChart(props: {
  /** The source data. Each element should be an object. */
  data?: ChartData<unknown> | undefined;
  /** Empty space around the container. */
  margin?: Partial<Margin> | undefined;
} & Omit<Dom, "data">): JSX.Element;

declare function Scatter(props: {
  fill?: string | undefined;
  /** The source data. Each element should be an object. */
  data?: ChartData<any> | undefined;
  /** Renders line connecting individual points. */
  line?: boolean | Props | React.ReactElement<SVGElement, string | React.JSXElementConstructor<any>> | ((props: any) => React.ReactElement<SVGElement>) | undefined;
  /** The name of data. */
  name?: string | undefined;
  /** The id of XAxis which is corresponding to the data. Required when there are multiple XAxes. */
  xAxisId?: AxisId | undefined;
  /** The id of YAxis which is corresponding to the data. Required when there are multiple YAxes. */
  yAxisId?: AxisId | undefined;
} & Omit<Dom, "fill" | "data" | "name">): JSX.Element;

declare function Cell(props: {
  /** The fill color. */
  fill?: string | undefined;
  /** The stroke color. */
  stroke?: string | undefined;
} & Omit<Dom, "fill" | "stroke">): JSX.Element;

declare function StatCard(props: {
  /** Label displayed above the value. */
  label: string;
  /** Inline metadata rendered next to the label, e.g. a scope or info tooltip. */
  labelEnd?: React.ReactNode;
  /** Value, trend, and optional caption content. */
  children: React.ReactNode;
  /** Makes the card an interactive button when provided. */
  onClick?: React.MouseEventHandler<HTMLButtonElement> | undefined;
  /** Additional classes for the card. */
  className?: string | undefined;
} & Omit<Dom, "label" | "children" | "onClick" | "className">): JSX.Element;

declare function StatCardValue(props: {
  /** Value content. */
  children: React.ReactNode;
  /** Additional classes for the value. */
  className?: string | undefined;
} & Omit<Dom, "children" | "className">): JSX.Element;

declare function StatCardValueRow(props: {
  /** Value and trend content. */
  children: React.ReactNode;
  /** Additional classes for the value row. */
  className?: string | undefined;
} & Omit<Dom, "children" | "className">): JSX.Element;

declare function StatCardTrend(props: {
  /** Trend content, such as a percentage. */
  children: React.ReactNode;
  /** Direction represented by the arrow icon. */
  direction: "up" | "down";
  /** Semantic color for the trend. */
  sentiment?: "positive" | "negative" | "neutral";
  /** Additional classes for the trend. */
  className?: string | undefined;
} & Omit<Dom, "children" | "direction" | "className">): JSX.Element;

declare function StatCardCaption(props: {
  /** Secondary line content. */
  children: React.ReactNode;
  /** Additional classes for the caption. */
  className?: string | undefined;
} & Omit<Dom, "children" | "className">): JSX.Element;

declare function StatsRow(props: {
  /** Stat cards to lay out in the row. */
  children: React.ReactNode;
  /** Fixed track count; cards wrap onto extra rows. Defaults to auto-fit. */
  columns?: 3 | 4;
  /** Additional classes for the row. */
  className?: string | undefined;
} & Omit<Dom, "children" | "className">): JSX.Element;

declare function BarList(props: {
  data: BarListItem[];
  max?: number | undefined;
} & Omit<Dom, "data" | "max">): JSX.Element;

declare function Heatmap(props: {
  data: HeatmapRow[];
  baseColor?: string | undefined;
  maxValue?: number | undefined;
} & Omit<Dom, "data">): JSX.Element;

declare function SegmentedBar(props: {
  segments: SegmentedBarSegment[];
  total?: number | undefined;
  /** Hairline gaps between segments so same-colored neighbours stay distinguishable. */
  separated?: boolean | undefined;
  /** `list` (default) puts each segment on its own row with the value at the */
  legend?: "list" | "inline";
} & Dom): JSX.Element;

declare function BulletChart(props: {
  value: number;
  forecast: number;
  limit: number;
  valueLabel?: string | undefined;
  forecastLabel?: string | undefined;
  limitLabel?: string | undefined;
  valueColor?: string | undefined;
  forecastColor?: string | undefined;
  limitColor?: string | undefined;
  max?: number | undefined;
} & Omit<Dom, "value" | "max">): JSX.Element;

declare function IconPlusLarge(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconMinusSmall(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconArrowRotateCounterClockwise(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconArrowUp(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconArrowDown(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconCheckmark2Medium(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconCrossMedium(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconChevronDownMedium(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconChevronRight(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconBell(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconCrossSmall(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function InputGroup(props: {
  variant?: "form" | "header" | "inline" | "default";
} & Dom): JSX.Element;

declare function InputGroupAddon(props: {
  align?: "inline-start" | "inline-end" | "block-start" | "block-end";
} & Dom): JSX.Element;

declare function InputGroupInput(props: {
  /** The value of the input. Use when controlled. */
  value?: string | number | readonly string[] | undefined;
  type?: React.HTMLInputTypeAttribute | undefined;
} & Omit<Dom, "value" | "type">): JSX.Element;

declare function IconCheckCircle2(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconCheckCircle2Fill(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconFolder2(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconLoader(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function Spinner(props: {
  /** Icon size in pixels. */
  size?: number | undefined;
  className?: string | undefined;
  /** Selects the spinner artwork: worklog circle, sidebar circle, loader, or */
  variant?: "fill" | "circle" | "loader" | "dots";
  /** Use when adjacent visible text (button label, row copy, tooltip) */
  decorative?: true | undefined;
  /** Sentence-case text equivalent of the pending state. */
  label?: string | undefined;
  /** `true` (default) exposes the label through a `role="status"` live */
  announce?: boolean | undefined;
} & Omit<Dom, "size" | "className" | "label">): JSX.Element;

declare function IconChainLink4(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconCircleOutline(props: {
} & Dom): JSX.Element;

declare function IconCircleCheck(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconCircleCheckFill(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconCircleDashed(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconCircleHalfFill(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconClock(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconFolder1(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconKanbanView(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconLightBulb(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconListBullets(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconLoadingCircle(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconMagnifyingGlass(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function IconPullRequest(props: {
  ariaHidden?: boolean | undefined;
  maskId?: string | undefined;
} & Dom): JSX.Element;

declare function PageLayout(props: {
  /** Also names the browser tab. `title` and `subtitle` are hidden when the page is shown inline in a message and shown when it is opened on its own. For a heading that should show inline too, write it as an element in the content instead. */
  title?: string | undefined;
  /** One sentence under the title saying what the page shows. */
  subtitle?: string | undefined;
  /** Puts a light/dark switch at the top right of the page when it is opened on its own. Left out inline, where the page follows the app's theme. */
  themeToggle?: boolean | undefined;
  children?: React.ReactNode;
} & Omit<Dom, "title" | "children">): JSX.Element;

declare function Footnote(props: {
  className?: string | undefined;
  children?: React.ReactNode;
} & Omit<Dom, "className" | "children">): JSX.Element;

declare function Diagram(props: {
  nodes: { id: string; title: string; sub?: string; group?: string; selected?: boolean; }[];
  edges: { from: string; to: string; label?: string; dim?: boolean; }[];
  groups?: { id: string; label: string; }[] | undefined;
  direction?: "LR" | "TB";
  /** Called with the node's id. Makes nodes focusable and clickable. */
  onNodeClick?: ((id: string) => void) | undefined;
} & Omit<Dom, "direction">): JSX.Element;

declare function ZoomArea(props: {
  /** 1 is the content's own size. Above 1 the content pans inside the area by drag or scroll, and hover tooltips inside it (chart tooltips) sit away from the pointer. */
  zoom: number;
  children?: React.ReactNode;
} & Omit<Dom, "children">): JSX.Element;

declare function ZoomControls(props: {
  value: number;
  onValueChange: (value: number) => void;
} & Omit<Dom, "value">): JSX.Element;

declare function Table(props: {
  /** Rows sit flush in the card, 40px tall at least, with a 1px rule under the */
  rowLines?: boolean | undefined;
  tableClassName?: string | undefined;
  tableStyle?: React.CSSProperties | undefined;
  /** When true, remove the table's card chrome for nesting inside another card. */
  flush?: boolean | undefined;
  /** Classes for the scroll viewport, e.g. a `max-h-*` cap so rows scroll. */
  viewportClassName?: string | undefined;
  /** Which axes the viewport scrolls on; "both" when a height cap makes rows scroll too. */
  scrollOrientation?: "horizontal" | "vertical" | "both";
  /** Keep the header row in view while rows scroll. Needs a viewport height cap and a vertical `scrollOrientation`. */
  stickyHeader?: boolean | undefined;
  /** Keep the first column in view while the table scrolls sideways. */
  stickyFirstColumn?: boolean | undefined;
  /** Positioned content over the card, outside the scrolling viewport (e.g. a "+N columns" hint). */
  overlay?: React.ReactNode;
  /** When true, table cells wrap text instead of using whitespace-nowrap. */
  wrapText?: boolean | undefined;
} & Dom): JSX.Element;

declare function TableBody(props: {
} & Dom): JSX.Element;

declare function TableCell(props: {
  /** Use compact height */
  compact?: boolean | undefined;
  /** Mark the cell as the positioning context for a stretched `TableRowAction`, */
  interactive?: boolean | undefined;
} & Dom): JSX.Element;

declare function TableHead(props: {
  /** Use compact height */
  compact?: boolean | undefined;
} & Dom): JSX.Element;

declare function SortableTableHead(props: {
  /** This column's current direction; leave unset on the columns that are not sorted. */
  sort?: "asc" | "desc";
  /** Called on click. The page decides the next direction. */
  onSort: () => void;
  /** "end" for a right-aligned (numeric) column: the arrow goes before the label. */
  align?: "start" | "end";
  className?: string | undefined;
  style?: React.CSSProperties | undefined;
  children?: React.ReactNode;
} & Omit<Dom, "className" | "style" | "children">): JSX.Element;

declare function TableHeader(props: {
} & Dom): JSX.Element;

declare function TableRow(props: {
  /** Enable hover highlight effect */
  highlight?: boolean | undefined;
  /** Mark the row as the positioning context for a stretched `TableRowAction`, */
  interactive?: boolean | undefined;
} & Dom): JSX.Element;

type SimpleComboboxOption = { value: string; label: string; disabled?: boolean; tooltip?: string; icon?: React.ReactNode; };

type SimpleSelectOption = { value: string; label: string; };

type ChartConfig = { [x: string]: { label?: React.ReactNode; icon?: React.ComponentType<{ className?: string; }>; color?: string; }; };

type TooltipPayload = readonly TooltipPayloadEntry[];

type TooltipPayloadEntry = Payload<ValueType, NameType>;

type GridLineType = boolean | React.SVGProps<SVGLineElement> | React.ReactElement<SVGElement, string | React.JSXElementConstructor<any>> | ((props: GridLineTypeFunctionProps) => React.ReactElement<SVGElement>);

type ImplicitLabelType = string | number | boolean | React.ReactElement<SVGElement, string | React.JSXElementConstructor<any>> | ((props: any) => RenderableText | React.ReactElement) | (Omit<React.SVGProps<SVGTextElement>, "viewBox"> & LabelProps & { dataKey?: DataKey<any>; });

type AxisDomain = readonly string[] | readonly number[] | readonly [AxisDomainItem, AxisDomainItem] | (([dataMin, dataMax]: NumberDomain, allowDataOverflow: boolean) => NumberDomain);

type AxisTick = string | number;

type AxisInterval = number | "preserveStart" | "preserveEnd" | "preserveStartEnd" | "equidistantPreserveStart" | "equidistantPreserveEnd";

type ImplicitLabelListType = boolean | LabelContentType | Props;

type RectRadius = number | [number, number, number, number];

type AxisId = string | number;

type StackId = string | number;

type CurveType = "step" | "linear" | "basis" | "basisClosed" | "basisOpen" | "bumpX" | "bumpY" | "bump" | "linearClosed" | "natural" | "monotoneX" | "monotoneY" | "monotone" | "stepBefore" | "stepAfter" | CurveFactory;

type DotType = boolean | ((props: DotItemDotProps) => React.ReactNode) | Partial<Props> | React.ReactElement<React.SVGProps<SVGElement>, string | React.JSXElementConstructor<any>>;

type ChartData<DataPointType = unknown> = ReadonlyArray<DataPointType>;

type PieLabel = boolean | LabelListPropsWithPosition | Partial<PieLabelRenderProps> | ((props: PieLabelRenderProps) => React.ReactNode | React.ReactElement<SVGElement>) | React.ReactElement<SVGElement, string | React.JSXElementConstructor<any>>;

type PieLabelLine = boolean | React.SVGProps<SVGPathElement> | React.ReactElement<SVGElement, string | React.JSXElementConstructor<any>> | ((props: any) => React.ReactElement<SVGElement>);

type BarListItem = { label: string; value: number; color?: string; valueLabel?: string; href?: React.AnchorHTMLAttributes<HTMLAnchorElement>["href"]; onClick?: React.ButtonHTMLAttributes<HTMLButtonElement>["onClick"]; };

type HeatmapRow = { label: string; cells: Array<{ label: string; value: number; }>; };

type SegmentedBarSegment = { label: string; value: number; color?: string; valueLabel?: React.ReactNode; href?: React.AnchorHTMLAttributes<HTMLAnchorElement>["href"]; onClick?: React.ButtonHTMLAttributes<HTMLButtonElement>["onClick"]; render?: React.ReactElement; };
