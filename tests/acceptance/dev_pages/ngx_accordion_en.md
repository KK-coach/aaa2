# ngx_accordion_en

- URL: https://valor-software.com/ngx-bootstrap/components/accordion
- nyelv: en
- site: This page belongs to the website valor-software.com, whose home page is titled “Angular Bootstrap”.
- blokkok (content régió): 212

Blokkonként: azonosító, típus, heading-útvonal, szöveg (táblázatsornál a cellák az oszlopfejléccel).

- **b0** `title`: Angular Bootstrap
- **b74** `list_item`: Home
- **b75** `list_item`: / components
- **b76** `list_item`: / accordion
- **b77** `heading`: Accordion
- **b78** `paragraph` (Accordion): Displays collapsible content panels for presenting information in a limited amount of space
- **b79** `paragraph` (Accordion): The accordion component builds on top of the collapse directive to provide a list of items, with collapsible bodies that are collapsed or expanded by clicking on the item's header.
- **b80** `paragraph` (Accordion): The easiest way to add an accordion component to your app (will be added to the root module)
- **b81** `list_item` (Accordion): Overview
- **b82** `list_item` (Accordion): API
- **b83** `list_item` (Accordion): Examples
- **b84** `heading`: Basic
- **b85** `paragraph` (Accordion › Basic): #
- **b86** `paragraph` (Accordion › Basic): Click headers to expand/collapse content that is broken into logical sections, much like tabs.
- **b87** `other` (Accordion › Basic): Static Header
- **b88** `other` (Accordion › Basic): Another group
- **b89** `other` (Accordion › Basic): Another group
- **b90** `other` (Accordion › Basic): Another group
- **b91** `list_item` (Accordion › Basic): template
- **b92** `list_item` (Accordion › Basic): component
- **b93** `code` (Accordion › Basic)

  ```
  <accordion>
  <accordion-group heading = "Static Header" >
  This content is straight in the template.
  </accordion-group>
  <accordion-group heading = "Another group" >
  <p> Some content </p>
  </accordion-group>
  <accordion-group heading = "Another group" >
  <p> Some content </p>
  </accordion-group>
  <accordion-group heading = "Another group" >
  <p> Some content </p>
  </accordion-group>
  </accordion>
  ```

- **b94** `code` (Accordion › Basic)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-basic' ,
  templateUrl : './basic.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionBasicComponent {}
  ```

- **b95** `heading`: With animation
- **b96** `paragraph` (Accordion › With animation): #
- **b97** `paragraph` (Accordion › With animation): Use input property or config property isAnimated to enable/disable animation
- **b98** `other` (Accordion › With animation): Static Header
- **b99** `other` (Accordion › With animation): Another group
- **b100** `other` (Accordion › With animation): Another group
- **b101** `other` (Accordion › With animation): Another group
- **b102** `list_item` (Accordion › With animation): template
- **b103** `list_item` (Accordion › With animation): component
- **b104** `code` (Accordion › With animation)

  ```
  <accordion [ isAnimated ] = "true" >
  <accordion-group heading = "Static Header" >
  This content is straight in the template.
  </accordion-group>
  <accordion-group heading = "Another group" >
  <p> Some content </p>
  </accordion-group>
  <accordion-group heading = "Another group" >
  <p> Some content </p>
  </accordion-group>
  <accordion-group heading = "Another group" >
  <p> Some content </p>
  </accordion-group>
  </accordion>
  ```

- **b105** `code` (Accordion › With animation)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-animation' ,
  templateUrl : './animated.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionAnimatedComponent {}
  ```

- **b106** `heading`: Group opening event
- **b107** `paragraph` (Accordion › Group opening event): #
- **b108** `paragraph` (Accordion › Group opening event): Accordion with isOpenChange event listener.
- **b109** `other` (Accordion › Group opening event): Group without isOpenChange event listener
- **b110** `other` (Accordion › Group opening event): Group with isOpenChange event listener
- **b111** `other` (Accordion › Group opening event): Group with isOpenChange event listener
- **b112** `list_item` (Accordion › Group opening event): template
- **b113** `list_item` (Accordion › Group opening event): component
- **b114** `code` (Accordion › Group opening event)

  ```
  <accordion>
  <accordion-group heading = "Group without isOpenChange event listener" >
  <p> Some content </p>
  </accordion-group>
  <accordion-group heading = "Group with isOpenChange event listener" ( isOpenChange ) = "log($event)" >
  <p> Some content </p>
  </accordion-group>
  <accordion-group heading = "Group with isOpenChange event listener" ( isOpenChange ) = "log($event)" >
  <p> Some content </p>
  </accordion-group>
  </accordion>
  ```

- **b115** `code` (Accordion › Group opening event)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-open-event' ,
  templateUrl : './open-event.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionOpenEventComponent {
  log ( event : boolean ) {
  console . log (` Accordion has been $ { event ? 'opened' : 'closed' }`);
  }
  }
  ```

- **b116** `heading`: Custom HTML
- **b117** `paragraph` (Accordion › Custom HTML): #
- **b118** `other` (Accordion › Custom HTML): I can have markup! Some HTML here
- **b119** `other` (Accordion › Custom HTML): I can have markup, too!
- **b120** `list_item` (Accordion › Custom HTML): template
- **b121** `list_item` (Accordion › Custom HTML): component
- **b122** `code` (Accordion › Custom HTML)

  ```
  <accordion>
  <accordion-group>
  <button
  class = "btn btn-link btn-block justify-content-between d-flex w-100 shadow-none"
  accordion-heading type = "button" >
  <div class = "pull-left float-left" > I can have markup! </div>
  <span class = "badge badge-secondary bg-secondary float-right pull-right" > Some HTML here </span>
  </button>
  This is just some content to illustrate fancy headings.
  </accordion-group>
  <accordion-group>
  <button class = "btn btn-link shadow-none" accordion-heading type = "button" >
  I can have markup, too!
  </button>
  <span class = "badge badge-secondary bg-secondary center" > And some HTML here </span>
  </accordion-group>
  </accordion>
  ```

- **b123** `code` (Accordion › Custom HTML)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  import { getBsVer , IBsVersion } from 'ngx-bootstrap/utils' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-custom-html' ,
  templateUrl : './custom-html.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionCustomHTMLComponent {
  get _getBsVer (): IBsVersion {
  return getBsVer ();
  }
  }
  ```

- **b124** `heading`: Disabled
- **b125** `paragraph` (Accordion › Disabled): #
- **b126** `paragraph` (Accordion › Disabled): Enable / Disable first panel
- **b127** `other` (Accordion › Disabled): Static Header
- **b128** `other` (Accordion › Disabled): Content 1
- **b129** `other` (Accordion › Disabled): Content 2
- **b130** `list_item` (Accordion › Disabled): template
- **b131** `list_item` (Accordion › Disabled): component
- **b132** `code` (Accordion › Disabled)

  ```
  <p>
  <button type = "button" class = "btn btn-primary btn-sm" ( click ) = "isFirstDisabled = !isFirstDisabled" >
  Enable / Disable first panel
  </button>
  </p>
  
  <accordion>
  <accordion-group heading = "Static Header"
  [ isDisabled ] = "isFirstDisabled" >
  This content is straight in the template.
  </accordion-group>
  <accordion-group heading = "Content 1" >
  <p> accordion 1 </p>
  </accordion-group>
  <accordion-group heading = "Content 2" >
  <p> accordion 2 </p>
  </accordion-group>
  </accordion>
  ```

- **b133** `code` (Accordion › Disabled)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-disabled' ,
  templateUrl : './disabled.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionDisabledComponent {
  isFirstDisabled = false ;
  }
  ```

- **b134** `heading`: Initially opened
- **b135** `paragraph` (Accordion › Initially opened): #
- **b136** `other` (Accordion › Initially opened): Content 1
- **b137** `other` (Accordion › Initially opened): Initially expanded
- **b138** `other` (Accordion › Initially opened): This content is straight in the template.
- **b139** `other` (Accordion › Initially opened): Content 2
- **b140** `list_item` (Accordion › Initially opened): template
- **b141** `list_item` (Accordion › Initially opened): component
- **b142** `code` (Accordion › Initially opened)

  ```
  <accordion>
  <accordion-group heading = "Content 1" >
  <p> accordion 1 </p>
  </accordion-group>
  <accordion-group heading = "Initially expanded"
  [ isOpen ] = "isFirstOpen" >
  This content is straight in the template.
  </accordion-group>
  <accordion-group heading = "Content 2" >
  <p> accordion 3 </p>
  </accordion-group>
  </accordion>
  ```

- **b143** `code` (Accordion › Initially opened)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-opened' ,
  templateUrl : './opened.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionOpenedComponent {
  isFirstOpen = true ;
  }
  ```

- **b144** `heading`: Dynamic accordion
- **b145** `paragraph` (Accordion › Dynamic accordion): #
- **b146** `paragraph` (Accordion › Dynamic accordion): Add Group Item
- **b147** `other` (Accordion › Dynamic accordion): Dynamic Group Header - 1
- **b148** `other` (Accordion › Dynamic accordion): Dynamic Group Header - 2
- **b149** `list_item` (Accordion › Dynamic accordion): template
- **b150** `list_item` (Accordion › Dynamic accordion): component
- **b151** `code` (Accordion › Dynamic accordion)

  ```
  <p>
  <button type = "button" class = "btn btn-primary btn-sm" ( click ) = "addGroupItem()" >
  Add Group Item
  </button>
  </p>
  
  <accordion>
  @for (group of groups; track group) {
  <accordion-group [ heading ] = "group.title" >
  {{ group?.content }}
  </accordion-group>
  }
  </accordion>
  ```

- **b152** `code` (Accordion › Dynamic accordion)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-dynamic' ,
  templateUrl : './dynamic.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionDynamicComponent {
  groups = [
  {
  title : 'Dynamic Group Header - 1' ,
  content : 'Dynamic Group Body - 1'
  },
  {
  title : 'Dynamic Group Header - 2' ,
  content : 'Dynamic Group Body - 2'
  }
  ];
  
  addGroupItem (): void {
  this . groups . push ({
  title : ` Dynamic Group Header - $ { this . groups . length + 1 }`,
  content : ` Dynamic Group Body - $ { this . groups . length + 1 }`
  });
  }
  }
  ```

- **b153** `heading`: Dynamic body content
- **b154** `paragraph` (Accordion › Dynamic body content): #
- **b155** `other` (Accordion › Dynamic body content): Dynamic Body Content
- **b156** `other` (Accordion › Dynamic body content): Content 2
- **b157** `other` (Accordion › Dynamic body content): Content 3
- **b158** `list_item` (Accordion › Dynamic body content): template
- **b159** `list_item` (Accordion › Dynamic body content): component
- **b160** `code` (Accordion › Dynamic body content)

  ```
  <accordion>
  <accordion-group heading = "Dynamic Body Content" >
  <p> The body of the accordion group grows to fit the contents </p>
  <button type = "button" class = "btn btn-primary btn-sm" ( click ) = "addItem()" > Add
  Item
  </button>
  <button type = "button" class = "btn btn-primary btn-sm ms-3 ml-3" ( click ) = "removeItem()" > Remove
  Item
  </button>
  @for (item of items; track item) {
  <div> {{item}} </div>
  }
  </accordion-group>
  <accordion-group heading = "Content 2" >
  <p> accordion 2 </p>
  </accordion-group>
  <accordion-group heading = "Content 3" >
  <p> accordion 3 </p>
  </accordion-group>
  </accordion>
  ```

- **b161** `code` (Accordion › Dynamic body content)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-dynamic-body' ,
  templateUrl : './dynamic-body.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionDynamicBodyComponent {
  items = [ 'Item 1' , 'Item 2' , 'Item 3' ];
  
  addItem (): void {
  this . items . push (` Item $ { this . items . length + 1 }`);
  }
  
  removeItem (): void {
  this . items = this . items . slice ( 0 , this . items . length - 1 );
  }
  }
  ```

- **b162** `heading`: Manual toggle
- **b163** `paragraph` (Accordion › Manual toggle): #
- **b164** `paragraph` (Accordion › Manual toggle): Toggle last panel
- **b165** `other` (Accordion › Manual toggle): Content 1
- **b166** `other` (Accordion › Manual toggle): Content 2
- **b167** `other` (Accordion › Manual toggle): Last panel
- **b168** `paragraph` (Accordion › Manual toggle): accordion 3
- **b169** `list_item` (Accordion › Manual toggle): template
- **b170** `list_item` (Accordion › Manual toggle): component
- **b171** `code` (Accordion › Manual toggle)

  ```
  <p>
  <button type = "button" class = "btn btn-primary btn-sm"
  ( click ) = "isOpen = !isOpen" > Toggle last panel
  </button>
  </p>
  
  <accordion>
  <accordion-group heading = "Content 1" >
  <p> accordion 1 </p>
  </accordion-group>
  <accordion-group heading = "Content 2" >
  <p> accordion 2 </p>
  </accordion-group>
  <accordion-group [ isOpen ] = "isOpen" heading = "Last panel" >
  <p> accordion 3 </p>
  </accordion-group>
  </accordion>
  ```

- **b172** `code` (Accordion › Manual toggle)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-manual-toggle' ,
  templateUrl : './manual-toggle.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionManualToggleComponent {
  isOpen = true ;
  }
  ```

- **b173** `heading`: Open only one at a time
- **b174** `paragraph` (Accordion › Open only one at a time): #
- **b175** `other` (Accordion › Open only one at a time): Open only one at a time
- **b176** `other` (Accordion › Open only one at a time): Header
- **b177** `other` (Accordion › Open only one at a time): Content 1
- **b178** `other` (Accordion › Open only one at a time): Content 2
- **b179** `list_item` (Accordion › Open only one at a time): template
- **b180** `list_item` (Accordion › Open only one at a time): component
- **b181** `code` (Accordion › Open only one at a time)

  ```
  <div class = "checkbox" >
  <label>
  <input type = "checkbox" [( ngModel )] = "oneAtATime" >
  Open only one at a time
  </label>
  </div>
  
  <accordion [ closeOthers ] = "oneAtATime" >
  <accordion-group heading = "Header" >
  This content is straight in the template.
  </accordion-group>
  <accordion-group heading = "Content 1" >
  <p> Content 1 </p>
  </accordion-group>
  <accordion-group heading = "Content 2" >
  <p> Content 2 </p>
  </accordion-group>
  </accordion>
  ```

- **b182** `code` (Accordion › Open only one at a time)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-one-time' ,
  templateUrl : './one-at-a-time.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionOneAtATimeComponent {
  oneAtATime = true ;
  }
  ```

- **b183** `heading`: Styling
- **b184** `paragraph` (Accordion › Styling): #
- **b185** `other` (Accordion › Styling): Static Header, initially expanded
- **b186** `other` (Accordion › Styling): This content is straight in the template.
- **b187** `other` (Accordion › Styling): Content 1
- **b188** `other` (Accordion › Styling): Content 2
- **b189** `list_item` (Accordion › Styling): template
- **b190** `list_item` (Accordion › Styling): component
- **b191** `code` (Accordion › Styling)

  ```
  <accordion>
  <accordion-group heading = "Static Header, initially expanded"
  [ panelClass ] = "customClass"
  [ isOpen ] = "isFirstOpen" >
  This content is straight in the template.
  </accordion-group>
  <accordion-group heading = "Content 1" >
  <p> accordion 1 </p>
  </accordion-group>
  <accordion-group heading = "Content 2" panelClass = "customClass" >
  <p> accordion 2 </p>
  </accordion-group>
  </accordion>
  ```

- **b192** `code` (Accordion › Styling)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-styling' ,
  templateUrl : './styling.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionStylingComponent {
  customClass = 'customClass' ;
  isFirstOpen = true ;
  }
  ```

- **b193** `heading`: Configuring defaults
- **b194** `paragraph` (Accordion › Configuring defaults): #
- **b195** `other` (Accordion › Configuring defaults): Header
- **b196** `other` (Accordion › Configuring defaults): Content 1
- **b197** `other` (Accordion › Configuring defaults): Content 2
- **b198** `list_item` (Accordion › Configuring defaults): template
- **b199** `list_item` (Accordion › Configuring defaults): component
- **b200** `code` (Accordion › Configuring defaults)

  ```
  <accordion [ isAnimated ] = "true" >
  <accordion-group heading = "Header" >
  This content is straight in the template.
  </accordion-group>
  <accordion-group heading = "Content 1" >
  <p> Content 1 </p>
  </accordion-group>
  <accordion-group heading = "Content 2" >
  <p> Content 2 </p>
  </accordion-group>
  </accordion>
  ```

- **b201** `code` (Accordion › Configuring defaults)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  import { AccordionConfig } from 'ngx-bootstrap/accordion' ;
  
  // such override allows to keep some initial values
  
  export function getAccordionConfig (): AccordionConfig {
  return Object . assign ( new AccordionConfig (), { closeOthers : true });
  }
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-accordion-config' ,
  templateUrl : './config.html' ,
  providers : [{ provide : AccordionConfig , useFactory : getAccordionConfig }],
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAccordionConfigComponent {}
  ```

- **b202** `heading`: API Reference
- **b203** `code` (Accordion › API Reference)

  ```
  ng add ngx-bootstrap  --component accordion
  ```

- **b204** `code` (Accordion › API Reference)

  ```
  ### Standalone component usage
  import { BrowserAnimationsModule } from '@angular/platform-browser/animations';
  
  import { AccordionComponent, AccordionPanelComponent } from 'ngx-bootstrap/accordion';
  
  @Component({
    standalone: true,
    imports: [
      BrowserAnimationsModule,
      AccordionComponent,
      AccordionPanelComponent
      ...
    ]
  })
  export class AppComponent(){}
  
  Also should be added web-animations-js polyfill for IE browser (Edge)
  ### Module usage
  import { BrowserAnimationsModule } from '@angular/platform-browser/animations';
  
  import { AccordionModule } from 'ngx-bootstrap/accordion';
  
  @NgModule({
    imports: [
      BrowserAnimationsModule,
      AccordionModule,
      ...
    ]
  })
  export class AppModule(){}
  
  Also should be added web-animations-js polyfill for IE browser (Edge)
  ```

- **b205** `heading`: AccordionComponent
- **b206** `paragraph` (Accordion › API Reference › AccordionComponent): Displays collapsible content panels for presenting information in a limited amount of space.
- **b207** `table_row` (Accordion › API Reference › AccordionComponent): Selector
- **b208** `heading`: AccordionPanelComponent
- **b209** `heading`: Accordion heading
- **b210** `paragraph` (Accordion › API Reference › Accordion heading): Instead of using heading attribute on the accordion-group , you can use an accordion-heading attribute on any element inside of a group that will be used as group's header template.
- **b211** `table_row` (Accordion › API Reference › Accordion heading): Selector
- **b212** `heading`: AccordionConfig
- **b213** `paragraph` (Accordion › API Reference › AccordionConfig): Configuration service, provides default values for the AccordionComponent.
- **b214** `heading`: Properties
- **b215** `table_row` (Accordion › API Reference › Properties): closeOthers; Type: boolean Default value: false Whether the other panels should be closed when a panel is opened
- **b216** `table_row` (Accordion › API Reference › Properties): isAnimated; Type: boolean Default value: false turn on/off animation
- **b217** `heading`: Basic
- **b218** `card` (Accordion › API Reference › Basic): Static Header
- **b219** `card` (Accordion › API Reference › Basic): Another group
- **b220** `card` (Accordion › API Reference › Basic): Another group
- **b221** `card` (Accordion › API Reference › Basic): Another group
- **b222** `heading`: With animation
- **b223** `card` (Accordion › API Reference › With animation): Static Header
- **b224** `card` (Accordion › API Reference › With animation): Another group
- **b225** `card` (Accordion › API Reference › With animation): Another group
- **b226** `card` (Accordion › API Reference › With animation): Another group
- **b227** `heading`: Group opening event
- **b228** `card` (Accordion › API Reference › Group opening event): Group without isOpenChange event listener
- **b229** `card` (Accordion › API Reference › Group opening event): Group with isOpenChange event listener
- **b230** `card` (Accordion › API Reference › Group opening event): Group with isOpenChange event listener
- **b231** `heading`: Custom HTML
- **b232** `card` (Accordion › API Reference › Custom HTML): I can have markup! Some HTML here
- **b233** `card` (Accordion › API Reference › Custom HTML): I can have markup, too!
- **b234** `heading`: Disabled
- **b235** `card` (Accordion › API Reference › Disabled): Enable / Disable first panel
- **b236** `card` (Accordion › API Reference › Disabled): Static Header
- **b237** `card` (Accordion › API Reference › Disabled): Content 1
- **b238** `card` (Accordion › API Reference › Disabled): Content 2
- **b239** `heading`: Initially opened
- **b240** `card` (Accordion › API Reference › Initially opened): Content 1
- **b241** `card` (Accordion › API Reference › Initially opened): Initially expanded
- **b242** `card` (Accordion › API Reference › Initially opened): This content is straight in the template.
- **b243** `card` (Accordion › API Reference › Initially opened): Content 2
- **b244** `heading`: Dynamic accordion
- **b245** `card` (Accordion › API Reference › Dynamic accordion): Add Group Item
- **b246** `card` (Accordion › API Reference › Dynamic accordion): Dynamic Group Header - 1
- **b247** `card` (Accordion › API Reference › Dynamic accordion): Dynamic Group Header - 2
- **b248** `heading`: Dynamic body content
- **b249** `card` (Accordion › API Reference › Dynamic body content): Dynamic Body Content
- **b250** `card` (Accordion › API Reference › Dynamic body content): Content 2
- **b251** `card` (Accordion › API Reference › Dynamic body content): Content 3
- **b252** `heading`: Manual toggle
- **b253** `card` (Accordion › API Reference › Manual toggle): Toggle last panel
- **b254** `card` (Accordion › API Reference › Manual toggle): Content 1
- **b255** `card` (Accordion › API Reference › Manual toggle): Content 2
- **b256** `card` (Accordion › API Reference › Manual toggle): Last panel
- **b257** `card` (Accordion › API Reference › Manual toggle): accordion 3
- **b258** `heading`: Open only one at a time
- **b259** `card` (Accordion › API Reference › Open only one at a time): Open only one at a time
- **b260** `card` (Accordion › API Reference › Open only one at a time): Header
- **b261** `card` (Accordion › API Reference › Open only one at a time): Content 1
- **b262** `card` (Accordion › API Reference › Open only one at a time): Content 2
- **b263** `heading`: Styling
- **b264** `card` (Accordion › API Reference › Styling): Static Header, initially expanded
- **b265** `card` (Accordion › API Reference › Styling): This content is straight in the template.
- **b266** `card` (Accordion › API Reference › Styling): Content 1
- **b267** `card` (Accordion › API Reference › Styling): Content 2
- **b268** `heading`: Configuring defaults
- **b269** `card` (Accordion › API Reference › Configuring defaults): Header
- **b270** `card` (Accordion › API Reference › Configuring defaults): Content 1
- **b271** `card` (Accordion › API Reference › Configuring defaults): Content 2
- **b272** `other` (Accordion › API Reference › Configuring defaults): components
- **b273** `list_item` (Accordion › API Reference › Configuring defaults): Basic
- **b274** `list_item` (Accordion › API Reference › Configuring defaults): With animation
- **b275** `list_item` (Accordion › API Reference › Configuring defaults): Group opening event
- **b276** `list_item` (Accordion › API Reference › Configuring defaults): Custom HTML
- **b277** `list_item` (Accordion › API Reference › Configuring defaults): Disabled
- **b278** `list_item` (Accordion › API Reference › Configuring defaults): Initially opened
- **b279** `list_item` (Accordion › API Reference › Configuring defaults): Dynamic accordion
- **b280** `list_item` (Accordion › API Reference › Configuring defaults): Dynamic body content
- **b281** `list_item` (Accordion › API Reference › Configuring defaults): Manual toggle
- **b282** `list_item` (Accordion › API Reference › Configuring defaults): Open only one at a time
- **b283** `list_item` (Accordion › API Reference › Configuring defaults): Styling
- **b284** `list_item` (Accordion › API Reference › Configuring defaults): Configuring defaults
