# locked_ngx_tabs_api

- URL: https://valor-software.com/ngx-bootstrap/components/tabs?tab=api
- nyelv: en
- site: This page belongs to the website valor-software.com, whose home page is titled “Angular Bootstrap”.
- blokkok (content régió): 305

Blokkonként: azonosító, típus, heading-útvonal, szöveg (táblázatsornál a cellák az oszlopfejléccel).

- **b0** `title`: Angular Bootstrap
- **b74** `list_item`: Home
- **b75** `list_item`: / components
- **b76** `list_item`: / tabs
- **b77** `heading`: Tabs
- **b78** `paragraph` (Tabs): Add quick, dynamic tab functionality to transition through panes of local content, even via dropdown menus. Nested tabs are not supported.
- **b79** `paragraph` (Tabs): The easiest way to add the tabs component to your app (will be added to the root module)
- **b80** `list_item` (Tabs): Overview
- **b81** `list_item` (Tabs): API
- **b82** `list_item` (Tabs): Examples
- **b83** `heading`: Basic
- **b84** `paragraph` (Tabs › Basic): #
- **b85** `list_item` (Tabs › Basic): Basic title
- **b86** `list_item` (Tabs › Basic): Basic Title 1
- **b87** `list_item` (Tabs › Basic): Basic Title 2
- **b88** `other` (Tabs › Basic): Basic content
- **b89** `other` (Tabs › Basic): Basic content 1
- **b90** `other` (Tabs › Basic): Basic content 2
- **b91** `list_item` (Tabs › Basic): template
- **b92** `list_item` (Tabs › Basic): component
- **b93** `code` (Tabs › Basic)

  ```
  <div>
  <tabset>
  <tab heading = "Basic title" id = "tab1" > Basic content </tab>
  <tab heading = "Basic Title 1" > Basic content 1 </tab>
  <tab heading = "Basic Title 2" > Basic content 2 </tab>
  </tabset>
  </div>
  ```

- **b94** `code` (Tabs › Basic)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-basic' ,
  templateUrl : './basic.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsBasicComponent {}
  ```

- **b95** `heading`: Manual selection
- **b96** `paragraph` (Tabs › Manual selection): #
- **b97** `paragraph` (Tabs › Manual selection): You can select tabs directly from component
- **b98** `paragraph` (Tabs › Manual selection): Select second tab Select third tab
- **b99** `list_item` (Tabs › Manual selection): Static title
- **b100** `list_item` (Tabs › Manual selection): Static Title 1
- **b101** `list_item` (Tabs › Manual selection): Static Title 2
- **b102** `list_item` (Tabs › Manual selection): Static Title 3
- **b103** `other` (Tabs › Manual selection): Static content
- **b104** `other` (Tabs › Manual selection): Static content 1
- **b105** `other` (Tabs › Manual selection): Static content 2
- **b106** `other` (Tabs › Manual selection): Static content 3
- **b107** `list_item` (Tabs › Manual selection): template
- **b108** `list_item` (Tabs › Manual selection): component
- **b109** `code` (Tabs › Manual selection)

  ```
  <div>
  <p> You can select tabs directly from component </p>
  <p>
  <button type = "button" class = "btn btn-primary btn-sm" ( click ) = "selectTab(1)" > Select second tab </button>
  <button type = "button" class = "btn btn-primary btn-sm" ( click ) = "selectTab(2)" > Select third tab </button>
  </p>
  <hr/>
  <tabset # staticTabs >
  <tab heading = "Static title" > Static content </tab>
  <tab heading = "Static Title 1" > Static content 1 </tab>
  <tab heading = "Static Title 2" > Static content 2 </tab>
  <tab heading = "Static Title 3" > Static content 3 </tab>
  </tabset>
  </div>
  ```

- **b110** `code` (Tabs › Manual selection)

  ```
  import { Component , ViewChild , ChangeDetectionStrategy } from '@angular/core' ;
  import { TabsetComponent } from 'ngx-bootstrap/tabs' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-manual-selection' ,
  templateUrl : './manual-selection.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsManualSelectionComponent {
  @ViewChild ( 'staticTabs' , { static : false }) staticTabs ?: TabsetComponent ;
  
  selectTab ( tabId : number ) {
  if ( this . staticTabs ?. tabs [ tabId ]) {
  this . staticTabs . tabs [ tabId ]. active = true ;
  }
  }
  }
  ```

- **b111** `heading`: Disabled tabs
- **b112** `paragraph` (Tabs › Disabled tabs): #
- **b113** `paragraph` (Tabs › Disabled tabs): Tabs can be enabled or disabled by changing disabled input property
- **b114** `paragraph` (Tabs › Disabled tabs): Enable / Disable third tab
- **b115** `list_item` (Tabs › Disabled tabs): Static title
- **b116** `list_item` (Tabs › Disabled tabs): Static Title 1
- **b117** `list_item` (Tabs › Disabled tabs): Static Title 2
- **b118** `list_item` (Tabs › Disabled tabs): Static Title 3
- **b119** `other` (Tabs › Disabled tabs): Static content
- **b120** `other` (Tabs › Disabled tabs): Static content 1
- **b121** `other` (Tabs › Disabled tabs): Static content 2
- **b122** `other` (Tabs › Disabled tabs): Static content 3
- **b123** `list_item` (Tabs › Disabled tabs): template
- **b124** `list_item` (Tabs › Disabled tabs): component
- **b125** `code` (Tabs › Disabled tabs)

  ```
  <div>
  <p> Tabs can be enabled or disabled by changing <code> disabled </code> input property </p>
  <p>
  <button type = "button" class = "btn btn-primary btn-sm" ( click ) = "disableEnable()" >
  Enable / Disable third tab
  </button>
  </p>
  <hr/>
  <tabset # staticTabs >
  <tab heading = "Static title" > Static content </tab>
  <tab heading = "Static Title 1" > Static content 1 </tab>
  <tab heading = "Static Title 2" > Static content 2 </tab>
  <tab heading = "Static Title 3" > Static content 3 </tab>
  </tabset>
  </div>
  ```

- **b126** `code` (Tabs › Disabled tabs)

  ```
  import { Component , ViewChild , ChangeDetectionStrategy } from '@angular/core' ;
  import { TabsetComponent } from 'ngx-bootstrap/tabs' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-disabled' ,
  templateUrl : './disabled.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsDisabledComponent {
  @ViewChild ( 'staticTabs' , { static : false }) staticTabs ?: TabsetComponent ;
  
  disableEnable () {
  if ( this . staticTabs ?. tabs [ 2 ]) {
  this . staticTabs . tabs [ 2 ]. disabled = ! this . staticTabs . tabs [ 2 ]. disabled ;
  }
  }
  }
  ```

- **b127** `heading`: Dynamic tabs
- **b128** `paragraph` (Tabs › Dynamic tabs): #
- **b129** `paragraph` (Tabs › Dynamic tabs): Change quantity of tabs by manipulating tabs array
- **b130** `other` (Tabs › Dynamic tabs): Add new tab Remove all tabs
- **b131** `list_item` (Tabs › Dynamic tabs): Static title
- **b132** `list_item` (Tabs › Dynamic tabs): Dynamic Title 1
- **b133** `list_item` (Tabs › Dynamic tabs): Dynamic Title 2
- **b134** `list_item` (Tabs › Dynamic tabs): Dynamic Title 3 ❌
- **b135** `other` (Tabs › Dynamic tabs): Static content
- **b136** `other` (Tabs › Dynamic tabs): Dynamic content 1
- **b137** `other` (Tabs › Dynamic tabs): Dynamic content 2
- **b138** `other` (Tabs › Dynamic tabs): Dynamic content 3
- **b139** `list_item` (Tabs › Dynamic tabs): template
- **b140** `list_item` (Tabs › Dynamic tabs): component
- **b141** `code` (Tabs › Dynamic tabs)

  ```
  <div ( click ) = "$event.preventDefault()" >
  <p> Change quantity of tabs by manipulating tabs array </p>
  <button type = "button" class = "btn btn-primary btn-sm" ( click ) = "addNewTab()" >
  Add new tab
  </button>
  @if (tabs.length) {
  <button type = "button" class = "btn btn-primary btn-sm" ( click ) = "tabs = []" >
  Remove all tabs
  </button>
  }
  <hr/>
  <tabset>
  <tab heading = "Static title" > Static content </tab>
  @for (tabz of tabs; track tabz) {
  <tab
  [ heading ] = "tabz.title"
  [ active ] = "tabz.active"
  ( selectTab ) = "tabz.active = true"
  ( deselect ) = "tabz.active = false"
  [ disabled ] = "tabz.disabled"
  [ removable ] = "tabz.removable"
  ( removed ) = "removeTabHandler(tabz)"
  [ customClass ] = "tabz.customClass" >
  {{tabz?.content}}
  </tab>
  }
  </tabset>
  </div>
  ```

- **b142** `code` (Tabs › Dynamic tabs)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  interface ITab {
  title : string ;
  content : string ;
  removable : boolean ;
  disabled : boolean ;
  active ?: boolean ;
  customClass ?: string ;
  }
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-dynamic' ,
  changeDetection : ChangeDetectionStrategy . OnPush ,
  templateUrl : './dynamic.html' ,
  standalone : false
  })
  export class DemoTabsDynamicComponent {
  tabs : ITab [] = [
  { title : 'Dynamic Title 1' , content : 'Dynamic content 1' , removable : false , disabled : false },
  { title : 'Dynamic Title 2' , content : 'Dynamic content 2' , removable : false , disabled : false , active : true },
  { title : 'Dynamic Title 3' , content : 'Dynamic content 3' , removable : true , disabled : false }
  ];
  
  addNewTab (): void {
  const newTabIndex = this . tabs . length + 1 ;
  this . tabs . push ({
  title : ` Dynamic Title $ { newTabIndex }`,
  content : ` Dynamic content $ { newTabIndex }`,
  disabled : false ,
  removable : true
  });
  }
  
  removeTabHandler ( tab : ITab ): void {
  this . tabs . splice ( this . tabs . indexOf ( tab ), 1 );
  console . log ( 'Remove Tab handler' );
  }
  
  onTabSelect ( tab : ITab ): void {
  this . tabs . forEach ( t => t . active = false );
  tab . active = true ;
  }
  
  onTabDeselect ( tab : ITab ): void {
  tab . active = false ;
  }
  }
  ```

- **b143** `heading`: Pills
- **b144** `paragraph` (Tabs › Pills): #
- **b145** `list_item` (Tabs › Pills): Pills 1
- **b146** `list_item` (Tabs › Pills): Pills 2
- **b147** `other` (Tabs › Pills): Pills content 1
- **b148** `other` (Tabs › Pills): Pills content 2
- **b149** `list_item` (Tabs › Pills): template
- **b150** `list_item` (Tabs › Pills): component
- **b151** `code` (Tabs › Pills)

  ```
  <tabset type = "pills" >
  <tab heading = "Pills 1" > Pills content 1 </tab>
  <tab heading = "Pills 2" > Pills content 2 </tab>
  </tabset>
  ```

- **b152** `code` (Tabs › Pills)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-pills' ,
  templateUrl : './pills.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsPillsComponent {}
  ```

- **b153** `heading`: Vertical Pills
- **b154** `paragraph` (Tabs › Vertical Pills): #
- **b155** `list_item` (Tabs › Vertical Pills): Vertical 1
- **b156** `list_item` (Tabs › Vertical Pills): Vertical 2
- **b157** `other` (Tabs › Vertical Pills): Vertical content 1
- **b158** `other` (Tabs › Vertical Pills): Vertical content 2
- **b159** `list_item` (Tabs › Vertical Pills): template
- **b160** `list_item` (Tabs › Vertical Pills): component
- **b161** `code` (Tabs › Vertical Pills)

  ```
  <tabset [ vertical ] = "true" type = "pills" >
  <tab heading = "Vertical 1" > Vertical content 1 </tab>
  <tab heading = "Vertical 2" > Vertical content 2 </tab>
  </tabset>
  ```

- **b162** `code` (Tabs › Vertical Pills)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-vertical-pills' ,
  templateUrl : './vertical-pills.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsVerticalPillsComponent {}
  ```

- **b163** `heading`: Justified
- **b164** `paragraph` (Tabs › Justified): #
- **b165** `paragraph` (Tabs › Justified): Bootstrap 4 doesn't have justified classes
- **b166** `list_item` (Tabs › Justified): Justified
- **b167** `list_item` (Tabs › Justified): SJ
- **b168** `list_item` (Tabs › Justified): Long Justified
- **b169** `other` (Tabs › Justified): Justified content
- **b170** `other` (Tabs › Justified): Short Labeled Justified content
- **b171** `other` (Tabs › Justified): Long Labeled Justified content
- **b172** `list_item` (Tabs › Justified): template
- **b173** `list_item` (Tabs › Justified): component
- **b174** `code` (Tabs › Justified)

  ```
  <tabset [ justified ] = "true" >
  <tab heading = "Justified" > Justified content </tab>
  <tab heading = "SJ" > Short Labeled Justified content </tab>
  <tab heading = "Long Justified" > Long Labeled Justified content </tab>
  </tabset>
  ```

- **b175** `code` (Tabs › Justified)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-justified' ,
  templateUrl : './justified.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsJustifiedComponent {}
  ```

- **b176** `heading`: Custom class
- **b177** `paragraph` (Tabs › Custom class): #
- **b178** `list_item` (Tabs › Custom class): Static title
- **b179** `list_item` (Tabs › Custom class): Dynamic Title 1
- **b180** `list_item` (Tabs › Custom class): Dynamic Title 2
- **b181** `other` (Tabs › Custom class): Static content
- **b182** `other` (Tabs › Custom class): Dynamic content 1
- **b183** `other` (Tabs › Custom class): Dynamic content 2
- **b184** `list_item` (Tabs › Custom class): template
- **b185** `list_item` (Tabs › Custom class): component
- **b186** `code` (Tabs › Custom class)

  ```
  <tabset>
  <tab heading = "Static title" customClass = "customClass" > Static content </tab>
  @for (tabz of tabs; track tabz) {
  <tab
  [ heading ] = "tabz.title"
  [ customClass ] = "tabz.customClass" >
  {{tabz?.content}}
  </tab>
  }
  </tabset>
  ```

- **b187** `code` (Tabs › Custom class)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  interface ITab {
  title : string ;
  content : string ;
  removable ?: boolean ;
  disabled ?: boolean ;
  active ?: boolean ;
  customClass ?: string ;
  }
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-custom-class' ,
  templateUrl : './custom-class.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsCustomClassComponent {
  tabs : ITab [] = [
  {
  title : 'Dynamic Title 1' ,
  content : 'Dynamic content 1' ,
  customClass : 'customClass'
  },
  {
  title : 'Dynamic Title 2' ,
  content : 'Dynamic content 2' ,
  customClass : 'customClass'
  }
  ];
  }
  ```

- **b188** `heading`: Select event
- **b189** `paragraph` (Tabs › Select event): #
- **b190** `paragraph` (Tabs › Select event): You can subscribe to tab's select event
- **b191** `code` (Tabs › Select event)

  ```
  Event select is fired. The heading of the selected tab is: First tab
  ```

- **b192** `list_item` (Tabs › Select event): First tab
- **b193** `list_item` (Tabs › Select event): Second tab
- **b194** `heading`: Title
- **b195** `paragraph` (Tabs › Select event › Title): Lorem Ipsum is simply dummy text of the printing and typesetting industry. Lorem Ipsum has been the industry's standard dummy text ever since the 1500s, when an unknown printer took a galley of type and scrambled it to make a type specimen book.
- **b196** `heading`: Title 2
- **b197** `paragraph` (Tabs › Select event › Title 2): It has survived not only five centuries, but also the leap into electronic typesetting, remaining essentially unchanged. It was popularised in the 1960s with the release of Letraset sheets containing Lorem Ipsum passages
- **b198** `list_item` (Tabs › Select event › Title 2): template
- **b199** `list_item` (Tabs › Select event › Title 2): component
- **b200** `code` (Tabs › Select event › Title 2)

  ```
  <div class = "mb-3" >
  @if (value) {
  <pre class = "card card-block card-header" > Event select is fired. The heading of the selected tab is: {{value}} </pre>
  }
  </div>
  <tabset>
  <tab heading = "First tab" class = "mt-2" ( selectTab ) = "onSelect($event)" >
  <h4> Title </h4>
  <p> Lorem Ipsum is simply dummy text of the printing and typesetting industry.
  Lorem Ipsum has been the industry's standard dummy text ever since the 1500s,
  when an unknown printer took a galley of type and scrambled it to make a type specimen book. </p>
  </tab>
  <tab heading = "Second tab" class = "mt-2" ( selectTab ) = "onSelect($event)" >
  <h4> Title 2 </h4>
  <p> It has survived not only five centuries, but also the leap into electronic typesetting,
  remaining essentially unchanged. It was popularised in the 1960s with the release of
  Letraset sheets containing Lorem Ipsum passages </p>
  </tab>
  </tabset>
  ```

- **b201** `code` (Tabs › Select event › Title 2)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  import { TabDirective } from 'ngx-bootstrap/tabs' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-select-event' ,
  templateUrl : './select-event.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsSelectEventComponent {
  value ?: string ;
  onSelect ( data : TabDirective ): void {
  this . value = data . heading ();
  }
  }
  ```

- **b202** `heading`: Configuring defaults
- **b203** `paragraph` (Tabs › Configuring defaults): #
- **b204** `list_item` (Tabs › Configuring defaults): Config 1
- **b205** `list_item` (Tabs › Configuring defaults): Config 2
- **b206** `other` (Tabs › Configuring defaults): Config content 1
- **b207** `other` (Tabs › Configuring defaults): Config content 2
- **b208** `list_item` (Tabs › Configuring defaults): template
- **b209** `list_item` (Tabs › Configuring defaults): component
- **b210** `code` (Tabs › Configuring defaults)

  ```
  <tabset>
  <tab heading = "Config 1" > Config content 1 </tab>
  <tab heading = "Config 2" > Config content 2 </tab>
  </tabset>
  ```

- **b211** `code` (Tabs › Configuring defaults)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  import { TabsetConfig } from 'ngx-bootstrap/tabs' ;
  
  // such override allows to keep some initial values
  
  export function getTabsetConfig (): TabsetConfig {
  return Object . assign ( new TabsetConfig (), { type : 'pills' , isKeysAllowed : true });
  }
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-config' ,
  templateUrl : './config.html' ,
  providers : [{ provide : TabsetConfig , useFactory : getTabsetConfig }],
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsConfigComponent {}
  ```

- **b212** `heading`: Custom template
- **b213** `paragraph` (Tabs › Custom template): #
- **b214** `list_item` (Tabs › Custom template): Static
- **b215** `list_item` (Tabs › Custom template): Heading
- **b216** `list_item` (Tabs › Custom template): Tab 3
- **b217** `other` (Tabs › Custom template): Tab 1
- **b218** `other` (Tabs › Custom template): I've got an HTML heading. Pretty cool!
- **b219** `other` (Tabs › Custom template): Tab with html tags in heading
- **b220** `list_item` (Tabs › Custom template): template
- **b221** `list_item` (Tabs › Custom template): component
- **b222** `code` (Tabs › Custom template)

  ```
  <div>
  <tabset>
  <tab heading = "Static" >
  Tab 1
  </tab>
  <tab>
  <ng-template tabHeading >
  <span class = "badge badge-secondary bg-secondary" > Heading </span>
  </ng-template>
  I've got an HTML heading. Pretty cool!
  </tab>
  <tab>
  <ng-template tabHeading >
  <i><b> Tab 3 </b></i>
  </ng-template>
  Tab with html tags in heading
  </tab>
  </tabset>
  </div>
  ```

- **b223** `code` (Tabs › Custom template)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tabs-custom-template' ,
  templateUrl : './custom-template.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTabsCustomComponent {}
  ```

- **b224** `heading`: Dynamic content rendering
- **b225** `paragraph` (Tabs › Dynamic content rendering): #
- **b226** `paragraph` (Tabs › Dynamic content rendering): The Component inside the Tab is rendered, when the tab is activated and destroyed when the tab is left.
- **b227** `list_item` (Tabs › Dynamic content rendering): Sub-Component A activated
- **b228** `list_item` (Tabs › Dynamic content rendering): Tab A
- **b229** `list_item` (Tabs › Dynamic content rendering): Tab B
- **b230** `list_item` (Tabs › Dynamic content rendering): Tab C
- **b231** `paragraph` (Tabs › Dynamic content rendering): Sub-Component A
- **b232** `list_item` (Tabs › Dynamic content rendering): template
- **b233** `list_item` (Tabs › Dynamic content rendering): component
- **b234** `code` (Tabs › Dynamic content rendering)

  ```
  <p> The Component inside the Tab is rendered, when the tab is activated and destroyed when the tab is left. </p>
  <ul class = "eventlist" >
  @for (message of messages; track message) {
  <li> {{ message }} </li>
  }
  </ul>
  <tabset>
  <tab heading = "Tab A" # tabA = "tab" >
  @if (tabA.active) {
  <sub-component
  name = "A"
  ( onInit ) = "message('Sub-Component A activated')"
  ( onDestroy ) = "message('Sub-Component A destroyed')"
  ></sub-component>
  }
  </tab>
  
  <tab heading = "Tab B" # tabB = "tab" >
  @if (tabB.active) {
  <sub-component
  name = "B"
  ( onInit ) = "message('Sub-Component B activated')"
  ( onDestroy ) = "message('Sub-Component B destroyed')"
  ></sub-component>
  }
  </tab>
  
  <tab heading = "Tab C" # tabC = "tab" >
  @if (tabC.active) {
  <sub-component
  name = "C"
  ( onInit ) = "message('Sub-Component C activated')"
  ( onDestroy ) = "message('Sub-Component C destroyed')"
  ></sub-component>
  }
  </tab>
  </tabset>
  ```

- **b235** `code` (Tabs › Dynamic content rendering)

  ```
  import { ChangeDetectionStrategy , Component } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'dynamic-content-rendering' ,
  changeDetection : ChangeDetectionStrategy . OnPush ,
  templateUrl : './dynamic-content-rendering.html' ,
  styleUrls : [ './dynamic-content-rendering.css' ],
  standalone : false
  })
  export class DynamicContentRenderingComponent {
  
  messages : string [] = [];
  
  message ( s : string ) {
  this . messages . push ( s );
  }
  
  }
  ```

- **b236** `heading`: Accessibility
- **b237** `paragraph` (Tabs › Accessibility): Note that tabs can be given role="tablist" , role="tab" and role="tabpanel" attributes. These are appropriate for tabbed interfaces, as described in the WAI ARIA Authoring Practices .
- **b238** `paragraph` (Tabs › Accessibility): If your control element is targeting a single collapsible element - you should add the aria-controls attribute to the control element, containing the id of the collapsible element.
- **b239** `paragraph` (Tabs › Accessibility): To confirm the tab content opening you should use aria-selected property. If aria-selected="true" it indicates the tab control is activated and its associated panel is displayed.
- **b240** `paragraph` (Tabs › Accessibility): If you use a visible text element on the page as a label for a focusable element - you should add aria-labelledby . It refers to the tab element that controls the panel.
- **b241** `heading`: Keyboard interaction
- **b242** `table_row` (Tabs › Accessibility › Keyboard interaction): LEFT_ARROW; Move focus to previous tab
- **b243** `table_row` (Tabs › Accessibility › Keyboard interaction): RIGHT_ARROW; Move focus to next tab
- **b244** `table_row` (Tabs › Accessibility › Keyboard interaction): HOME; Move focus to first tab
- **b245** `table_row` (Tabs › Accessibility › Keyboard interaction): END; Move focus to last tab
- **b246** `table_row` (Tabs › Accessibility › Keyboard interaction): SPACE or ENTER; Switch to focused tab
- **b247** `heading`: Disable key navigations
- **b248** `paragraph` (Tabs › Disable key navigations): #
- **b249** `list_item` (Tabs › Disable key navigations): Tab1
- **b250** `list_item` (Tabs › Disable key navigations): Tab2
- **b251** `other` (Tabs › Disable key navigations): Tab1
- **b252** `other` (Tabs › Disable key navigations): Tab2
- **b253** `list_item` (Tabs › Disable key navigations): template
- **b254** `list_item` (Tabs › Disable key navigations): component
- **b255** `code` (Tabs › Disable key navigations)

  ```
  <tabset>
  <tab heading = "Tab1" > Tab1 </tab>
  <tab heading = "Tab2" > Tab2 </tab>
  </tabset>
  ```

- **b256** `code` (Tabs › Disable key navigations)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  import { TabsetConfig } from 'ngx-bootstrap/tabs' ;
  
  export function getTabsetConfig (): TabsetConfig {
  return Object . assign ( new TabsetConfig (), { type : 'tabs' , isKeysAllowed : false });
  }
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-disabled-key-navigations' ,
  templateUrl : './disabled-key-navigations.html' ,
  providers : [{ provide : TabsetConfig , useFactory : getTabsetConfig }],
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoDisabledKeyNavigationsComponent {
  }
  ```

- **b257** `heading`: Installation
- **b258** `code` (Tabs › Installation)

  ```
  ng add ngx-bootstrap  --component tabs
  ```

- **b259** `code` (Tabs › Installation)

  ```
  ### Standalone component usage
  import { TabsModule } from 'ngx-bootstrap/tabs';
  
  @Component({
    standalone: true,
    imports: [TabsModule,...]
  })
  export class AppComponent(){}
  
  ### Module usage
  import { TabsModule } from 'ngx-bootstrap/tabs';
  
  @NgModule({
    imports: [TabsModule,...]
  })
  export class AppModule(){}
  ```

- **b260** `table_row` (Tabs › Installation): Selector
- **b261** `table_row` (Tabs › Installation): Selector
- **b262** `heading`: TabHeadingDirective
- **b263** `paragraph` (Tabs › Installation › TabHeadingDirective): Should be used to mark
- **b264** `other` (Tabs › Installation › TabHeadingDirective): element as a template for tab heading
- **b265** `table_row` (Tabs › Installation › TabHeadingDirective): Selector
- **b266** `heading`: Basic
- **b267** `list_item` (Tabs › Installation › Basic): Basic title
- **b268** `list_item` (Tabs › Installation › Basic): Basic Title 1
- **b269** `list_item` (Tabs › Installation › Basic): Basic Title 2
- **b270** `other` (Tabs › Installation › Basic): Basic content
- **b271** `other` (Tabs › Installation › Basic): Basic content 1
- **b272** `other` (Tabs › Installation › Basic): Basic content 2
- **b273** `heading`: Manual selection
- **b274** `paragraph` (Tabs › Installation › Manual selection): You can select tabs directly from component
- **b275** `paragraph` (Tabs › Installation › Manual selection): Select second tab Select third tab
- **b276** `list_item` (Tabs › Installation › Manual selection): Static title
- **b277** `list_item` (Tabs › Installation › Manual selection): Static Title 1
- **b278** `list_item` (Tabs › Installation › Manual selection): Static Title 2
- **b279** `list_item` (Tabs › Installation › Manual selection): Static Title 3
- **b280** `other` (Tabs › Installation › Manual selection): Static content
- **b281** `other` (Tabs › Installation › Manual selection): Static content 1
- **b282** `other` (Tabs › Installation › Manual selection): Static content 2
- **b283** `other` (Tabs › Installation › Manual selection): Static content 3
- **b284** `heading`: Disabled tabs
- **b285** `paragraph` (Tabs › Installation › Disabled tabs): Tabs can be enabled or disabled by changing disabled input property
- **b286** `paragraph` (Tabs › Installation › Disabled tabs): Enable / Disable third tab
- **b287** `list_item` (Tabs › Installation › Disabled tabs): Static title
- **b288** `list_item` (Tabs › Installation › Disabled tabs): Static Title 1
- **b289** `list_item` (Tabs › Installation › Disabled tabs): Static Title 2
- **b290** `list_item` (Tabs › Installation › Disabled tabs): Static Title 3
- **b291** `other` (Tabs › Installation › Disabled tabs): Static content
- **b292** `other` (Tabs › Installation › Disabled tabs): Static content 1
- **b293** `other` (Tabs › Installation › Disabled tabs): Static content 2
- **b294** `other` (Tabs › Installation › Disabled tabs): Static content 3
- **b295** `heading`: Dynamic tabs
- **b296** `paragraph` (Tabs › Installation › Dynamic tabs): Change quantity of tabs by manipulating tabs array
- **b297** `other` (Tabs › Installation › Dynamic tabs): Add new tab Remove all tabs
- **b298** `list_item` (Tabs › Installation › Dynamic tabs): Static title
- **b299** `list_item` (Tabs › Installation › Dynamic tabs): Dynamic Title 1
- **b300** `list_item` (Tabs › Installation › Dynamic tabs): Dynamic Title 2
- **b301** `list_item` (Tabs › Installation › Dynamic tabs): Dynamic Title 3 ❌
- **b302** `other` (Tabs › Installation › Dynamic tabs): Static content
- **b303** `other` (Tabs › Installation › Dynamic tabs): Dynamic content 1
- **b304** `other` (Tabs › Installation › Dynamic tabs): Dynamic content 2
- **b305** `other` (Tabs › Installation › Dynamic tabs): Dynamic content 3
- **b306** `heading`: Pills
- **b307** `list_item` (Tabs › Installation › Pills): Pills 1
- **b308** `list_item` (Tabs › Installation › Pills): Pills 2
- **b309** `other` (Tabs › Installation › Pills): Pills content 1
- **b310** `other` (Tabs › Installation › Pills): Pills content 2
- **b311** `heading`: Vertical Pills
- **b312** `list_item` (Tabs › Installation › Vertical Pills): Vertical 1
- **b313** `list_item` (Tabs › Installation › Vertical Pills): Vertical 2
- **b314** `other` (Tabs › Installation › Vertical Pills): Vertical content 1
- **b315** `other` (Tabs › Installation › Vertical Pills): Vertical content 2
- **b316** `heading`: Justified
- **b317** `list_item` (Tabs › Installation › Justified): Justified
- **b318** `list_item` (Tabs › Installation › Justified): SJ
- **b319** `list_item` (Tabs › Installation › Justified): Long Justified
- **b320** `other` (Tabs › Installation › Justified): Justified content
- **b321** `other` (Tabs › Installation › Justified): Short Labeled Justified content
- **b322** `other` (Tabs › Installation › Justified): Long Labeled Justified content
- **b323** `heading`: Custom class
- **b324** `list_item` (Tabs › Installation › Custom class): Static title
- **b325** `list_item` (Tabs › Installation › Custom class): Dynamic Title 1
- **b326** `list_item` (Tabs › Installation › Custom class): Dynamic Title 2
- **b327** `other` (Tabs › Installation › Custom class): Static content
- **b328** `other` (Tabs › Installation › Custom class): Dynamic content 1
- **b329** `other` (Tabs › Installation › Custom class): Dynamic content 2
- **b330** `heading`: Select event
- **b331** `code` (Tabs › Installation › Select event)

  ```
  Event select is fired. The heading of the selected tab is: First tab
  ```

- **b332** `list_item` (Tabs › Installation › Select event): First tab
- **b333** `list_item` (Tabs › Installation › Select event): Second tab
- **b334** `heading`: Title
- **b335** `paragraph` (Tabs › Installation › Select event › Title): Lorem Ipsum is simply dummy text of the printing and typesetting industry. Lorem Ipsum has been the industry's standard dummy text ever since the 1500s, when an unknown printer took a galley of type and scrambled it to make a type specimen book.
- **b336** `heading`: Title 2
- **b337** `paragraph` (Tabs › Installation › Select event › Title 2): It has survived not only five centuries, but also the leap into electronic typesetting, remaining essentially unchanged. It was popularised in the 1960s with the release of Letraset sheets containing Lorem Ipsum passages
- **b338** `heading`: Configuring defaults
- **b339** `list_item` (Tabs › Installation › Configuring defaults): Config 1
- **b340** `list_item` (Tabs › Installation › Configuring defaults): Config 2
- **b341** `other` (Tabs › Installation › Configuring defaults): Config content 1
- **b342** `other` (Tabs › Installation › Configuring defaults): Config content 2
- **b343** `heading`: Custom template
- **b344** `list_item` (Tabs › Installation › Custom template): Static
- **b345** `list_item` (Tabs › Installation › Custom template): Heading
- **b346** `list_item` (Tabs › Installation › Custom template): Tab 3
- **b347** `other` (Tabs › Installation › Custom template): Tab 1
- **b348** `other` (Tabs › Installation › Custom template): I've got an HTML heading. Pretty cool!
- **b349** `other` (Tabs › Installation › Custom template): Tab with html tags in heading
- **b350** `heading`: Dynamic content rendering
- **b351** `paragraph` (Tabs › Installation › Dynamic content rendering): The Component inside the Tab is rendered, when the tab is activated and destroyed when the tab is left.
- **b352** `list_item` (Tabs › Installation › Dynamic content rendering): Sub-Component A activated
- **b353** `list_item` (Tabs › Installation › Dynamic content rendering): Tab A
- **b354** `list_item` (Tabs › Installation › Dynamic content rendering): Tab B
- **b355** `list_item` (Tabs › Installation › Dynamic content rendering): Tab C
- **b356** `paragraph` (Tabs › Installation › Dynamic content rendering): Sub-Component A
- **b357** `heading`: Accessibility
- **b358** `paragraph` (Tabs › Installation › Accessibility): Note that tabs can be given role="tablist" , role="tab" and role="tabpanel" attributes. These are appropriate for tabbed interfaces, as described in the WAI ARIA Authoring Practices .
- **b359** `paragraph` (Tabs › Installation › Accessibility): If your control element is targeting a single collapsible element - you should add the aria-controls attribute to the control element, containing the id of the collapsible element.
- **b360** `paragraph` (Tabs › Installation › Accessibility): To confirm the tab content opening you should use aria-selected property. If aria-selected="true" it indicates the tab control is activated and its associated panel is displayed.
- **b361** `paragraph` (Tabs › Installation › Accessibility): If you use a visible text element on the page as a label for a focusable element - you should add aria-labelledby . It refers to the tab element that controls the panel.
- **b362** `heading`: Keyboard interaction
- **b363** `table_row` (Tabs › Installation › Accessibility › Keyboard interaction): LEFT_ARROW; Move focus to previous tab
- **b364** `table_row` (Tabs › Installation › Accessibility › Keyboard interaction): RIGHT_ARROW; Move focus to next tab
- **b365** `table_row` (Tabs › Installation › Accessibility › Keyboard interaction): HOME; Move focus to first tab
- **b366** `table_row` (Tabs › Installation › Accessibility › Keyboard interaction): END; Move focus to last tab
- **b367** `table_row` (Tabs › Installation › Accessibility › Keyboard interaction): SPACE or ENTER; Switch to focused tab
- **b368** `heading`: Disable key navigations
- **b369** `list_item` (Tabs › Installation › Disable key navigations): Tab1
- **b370** `list_item` (Tabs › Installation › Disable key navigations): Tab2
- **b371** `other` (Tabs › Installation › Disable key navigations): Tab1
- **b372** `other` (Tabs › Installation › Disable key navigations): Tab2
- **b373** `other` (Tabs › Installation › Disable key navigations): components
- **b374** `list_item` (Tabs › Installation › Disable key navigations): TabsetComponent
- **b375** `list_item` (Tabs › Installation › Disable key navigations): TabDirective
- **b376** `list_item` (Tabs › Installation › Disable key navigations): TabHeadingDirective
- **b377** `list_item` (Tabs › Installation › Disable key navigations): TabsetConfig
