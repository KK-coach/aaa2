# locked_ngx_tooltip_api

- URL: https://valor-software.com/ngx-bootstrap/components/tooltip?tab=api
- nyelv: en
- site: This page belongs to the website valor-software.com, whose home page is titled “Angular Bootstrap”.
- blokkok (content régió): 180

Blokkonként: azonosító, típus, heading-útvonal, szöveg (táblázatsornál a cellák az oszlopfejléccel).

- **b0** `title`: Angular Bootstrap
- **b74** `list_item`: Home
- **b75** `list_item`: / components
- **b76** `list_item`: / tooltip
- **b77** `heading`: Tooltip
- **b78** `paragraph` (Tooltip): Inspired by the excellent Tipsy jQuery plugin written by Jason Frame. Tooltips are an updated version, which don’t rely on images, use CSS3 for animations, and much more.
- **b79** `paragraph` (Tooltip): The easiest way to add the tooltip component to your app (will be added to the root module)
- **b80** `list_item` (Tooltip): Overview
- **b81** `list_item` (Tooltip): API
- **b82** `list_item` (Tooltip): Examples
- **b83** `heading`: Basic
- **b84** `paragraph` (Tooltip › Basic): #
- **b85** `other` (Tooltip › Basic): Simple demo
- **b86** `list_item` (Tooltip › Basic): template
- **b87** `list_item` (Tooltip › Basic): component
- **b88** `code` (Tooltip › Basic)

  ```
  <button type = "button" class = "btn btn-primary"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus." >
  Simple demo
  </button>
  ```

- **b89** `code` (Tooltip › Basic)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-basic' ,
  templateUrl : './basic.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipBasicComponent {}
  ```

- **b90** `heading`: Placement
- **b91** `paragraph` (Tooltip › Placement): #
- **b92** `paragraph` (Tooltip › Placement): Four positioning options are available: top , right , bottom , and left . Besides that, auto option may be used to detect a position that fits the component on the screen.
- **b93** `other` (Tooltip › Placement): Tooltip on top
- **b94** `other` (Tooltip › Placement): Tooltip on right
- **b95** `other` (Tooltip › Placement): Tooltip auto
- **b96** `other` (Tooltip › Placement): Tooltip on left
- **b97** `other` (Tooltip › Placement): Tooltip on bottom
- **b98** `list_item` (Tooltip › Placement): template
- **b99** `list_item` (Tooltip › Placement): component
- **b100** `code` (Tooltip › Placement)

  ```
  <button type = "button" class = "btn btn-default btn-secondary mb-2"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  placement = "top" >
  Tooltip on top
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary mb-2"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  placement = "right" >
  Tooltip on right
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary mb-2"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  placement = "auto" >
  Tooltip auto
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary mb-2"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  placement = "left" >
  Tooltip on left
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary mb-2"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  placement = "bottom" >
  Tooltip on bottom
  </button>
  ```

- **b101** `code` (Tooltip › Placement)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-placement' ,
  templateUrl : './placement.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipPlacementComponent {}
  ```

- **b102** `heading`: Disable adaptive position
- **b103** `paragraph` (Tooltip › Disable adaptive position): #
- **b104** `paragraph` (Tooltip › Disable adaptive position): You can disable adaptive position via adaptivePosition input or config option
- **b105** `other` (Tooltip › Disable adaptive position): Tooltip on top
- **b106** `other` (Tooltip › Disable adaptive position): Tooltip on right
- **b107** `list_item` (Tooltip › Disable adaptive position): template
- **b108** `list_item` (Tooltip › Disable adaptive position): component
- **b109** `code` (Tooltip › Disable adaptive position)

  ```
  <button type = "button" class = "btn btn-default btn-secondary mb-2"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  [ adaptivePosition ] = "false"
  placement = "top" >
  Tooltip on top
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary mb-2"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  [ adaptivePosition ] = "false"
  placement = "right" >
  Tooltip on right
  </button>
  ```

- **b110** `code` (Tooltip › Disable adaptive position)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-adaptive-position' ,
  templateUrl : './adaptive-position.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipAdaptivePositionComponent {}
  ```

- **b111** `heading`: Dismiss on next click
- **b112** `paragraph` (Tooltip › Dismiss on next click): #
- **b113** `paragraph` (Tooltip › Dismiss on next click): Use the focus trigger to dismiss tooltips on the next click that the user makes.
- **b114** `other` (Tooltip › Dismiss on next click): Dismissible tooltip
- **b115** `list_item` (Tooltip › Dismiss on next click): template
- **b116** `list_item` (Tooltip › Dismiss on next click): component
- **b117** `code` (Tooltip › Dismiss on next click)

  ```
  <button type = "button" class = "btn btn-success"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  triggers = "focus" >
  Dismissible tooltip
  </button>
  ```

- **b118** `code` (Tooltip › Dismiss on next click)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-dismiss' ,
  templateUrl : './dismiss.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipDismissComponent {}
  ```

- **b119** `heading`: Dynamic Content
- **b120** `paragraph` (Tooltip › Dynamic Content): #
- **b121** `paragraph` (Tooltip › Dynamic Content): Pass a string as tooltip content
- **b122** `other` (Tooltip › Dynamic Content): Simple binding
- **b123** `list_item` (Tooltip › Dynamic Content): template
- **b124** `list_item` (Tooltip › Dynamic Content): component
- **b125** `code` (Tooltip › Dynamic Content)

  ```
  <button type = "button" class = "btn btn-info" [ tooltip ] = "content" >
  Simple binding
  </button>
  ```

- **b126** `code` (Tooltip › Dynamic Content)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-dynamic' ,
  templateUrl : './dynamic.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipDynamicComponent {
  content = 'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.' ;
  }
  ```

- **b127** `heading`: Custom content template
- **b128** `paragraph` (Tooltip › Custom content template): #
- **b129** `paragraph` (Tooltip › Custom content template): Create <ng-template #myId> with any html allowed by Angular, and provide template ref [tooltip]="myId" as tooltip content
- **b130** `other` (Tooltip › Custom content template): TemplateRef binding
- **b131** `list_item` (Tooltip › Custom content template): template
- **b132** `list_item` (Tooltip › Custom content template): component
- **b133** `code` (Tooltip › Custom content template)

  ```
  <ng-template # tolTemplate > Just another: {{content}} </ng-template>
  <button type = "button" class = "btn btn-warning" [ tooltip ] = "tolTemplate" >
  TemplateRef binding
  </button>
  ```

- **b134** `code` (Tooltip › Custom content template)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-custom-content' ,
  templateUrl : './custom-content.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipCustomContentComponent {
  content = 'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.' ;
  }
  ```

- **b135** `heading`: Dynamic Html
- **b136** `paragraph` (Tooltip › Dynamic Html): #
- **b137** `paragraph` (Tooltip › Dynamic Html): By using [innerHtml] inside ng-template you can display any dynamic html
- **b138** `other` (Tooltip › Dynamic Html): Show me tooltip with html
- **b139** `list_item` (Tooltip › Dynamic Html): template
- **b140** `list_item` (Tooltip › Dynamic Html): component
- **b141** `code` (Tooltip › Dynamic Html)

  ```
  <ng-template # popTemplate > Here we go: <div [ innerHtml ] = "html" ></div></ng-template>
  <button type = "button" class = "btn btn-success"
  [ tooltip ] = "popTemplate" >
  Show me tooltip with html
  </button>
  ```

- **b142** `code` (Tooltip › Dynamic Html)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-dynamic-html' ,
  templateUrl : './dynamic-html.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipDynamicHtmlComponent {
  html = `< span class = "btn-block btn-danger well-sm" > Never trust not sanitized HTML !!!</ span >`;
  }
  ```

- **b143** `heading`: Append to body
- **b144** `paragraph` (Tooltip › Append to body): #
- **b145** `paragraph` (Tooltip › Append to body): When you have some styles on a parent element that interfere with a tooltip, you’ll want to specify a container="body" so that the tooltip’s HTML will be appended to body. This will help to avoid rendering problems in more complex components (like our input groups, button groups, etc) or inside elements with overflow: hidden
- **b146** `other` (Tooltip › Append to body): Default tooltip
- **b147** `other` (Tooltip › Append to body): Tooltip appended to body
- **b148** `list_item` (Tooltip › Append to body): template
- **b149** `list_item` (Tooltip › Append to body): component
- **b150** `code` (Tooltip › Append to body)

  ```
  <div class = "row" style = " position : relative ; overflow : hidden ; padding - top : 10px ; " >
  <div class = "col-xs-12 col-12" >
  <button type = "button" class = "btn btn-danger"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus." >
  Default tooltip
  </button>
  <button type = "button" class = "btn btn-success"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus."
  container = "body" >
  Tooltip appended to body
  </button>
  </div>
  </div>
  ```

- **b151** `code` (Tooltip › Append to body)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-container' ,
  templateUrl : './container.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipContainerComponent {}
  ```

- **b152** `heading`: Configuring defaults
- **b153** `paragraph` (Tooltip › Configuring defaults): #
- **b154** `other` (Tooltip › Configuring defaults): Preconfigured tooltip
- **b155** `list_item` (Tooltip › Configuring defaults): template
- **b156** `list_item` (Tooltip › Configuring defaults): component
- **b157** `code` (Tooltip › Configuring defaults)

  ```
  <button type = "button" class = "btn btn-primary"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus." >
  Preconfigured tooltip
  </button>
  ```

- **b158** `code` (Tooltip › Configuring defaults)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  import { TooltipConfig } from 'ngx-bootstrap/tooltip' ;
  
  // such override allows to keep some initial values
  
  export function getAlertConfig (): TooltipConfig {
  return Object . assign ( new TooltipConfig (), {
  placement : 'right' ,
  container : 'body' ,
  delay : 500
  });
  }
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-config' ,
  templateUrl : './config.html' ,
  providers : [{ provide : TooltipConfig , useFactory : getAlertConfig }],
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipConfigComponent {}
  ```

- **b159** `heading`: Custom triggers
- **b160** `paragraph` (Tooltip › Custom triggers): #
- **b161** `paragraph` (Tooltip › Custom triggers): Desktop
- **b162** `other` (Tooltip › Custom triggers): Hover over me!
- **b163** `paragraph` (Tooltip › Custom triggers): Mobile
- **b164** `other` (Tooltip › Custom triggers): Click on me!
- **b165** `list_item` (Tooltip › Custom triggers): template
- **b166** `list_item` (Tooltip › Custom triggers): component
- **b167** `code` (Tooltip › Custom triggers)

  ```
  <div class = "row" >
  <div class = "col-xs-6 col-6" >
  <p> Desktop </p>
  <button type = "button" class = "btn btn-info"
  tooltip = "I will hide on click"
  triggers = "mouseenter:click" >
  Hover over me!
  </button>
  </div>
  
  <div class = "col-xs-6 col-6" >
  <p> Mobile </p>
  <button type = "button" class = "btn btn-info"
  tooltip = "I will hide on click"
  triggers = "click" >
  Click on me!
  </button>
  </div>
  </div>
  ```

- **b168** `code` (Tooltip › Custom triggers)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-triggers-custom' ,
  templateUrl : './triggers-custom.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipTriggersCustomComponent {}
  ```

- **b169** `heading`: Manual triggering
- **b170** `paragraph` (Tooltip › Manual triggering): #
- **b171** `paragraph` (Tooltip › Manual triggering): You can manage tooltip using its show() , hide() and toggle() methods. If you want to manage tooltip's state manually, use triggers=""
- **b172** `paragraph` (Tooltip › Manual triggering): This text has attached tooltip
- **b173** `other` (Tooltip › Manual triggering): Show Hide Toggle
- **b174** `list_item` (Tooltip › Manual triggering): template
- **b175** `list_item` (Tooltip › Manual triggering): component
- **b176** `code` (Tooltip › Manual triggering)

  ```
  <p>
  <span tooltip = "Hello there! I was triggered manually"
  triggers = "" # pop = "bs-tooltip" >
  This text has attached tooltip
  </span>
  </p>
  
  <button type = "button" class = "btn btn-success" ( click ) = "pop.show()" >
  Show
  </button>
  <button type = "button" class = "btn btn-warning" ( click ) = "pop.hide()" >
  Hide
  </button>
  <button type = "button" class = "btn btn-info" ( click ) = "pop.toggle()" >
  Toggle
  </button>
  ```

- **b177** `code` (Tooltip › Manual triggering)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-triggers-manual' ,
  templateUrl : './triggers-manual.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipTriggersManualComponent {}
  ```

- **b178** `heading`: Component level styling
- **b179** `paragraph` (Tooltip › Component level styling): #
- **b180** `other` (Tooltip › Component level styling): I have component level styling
- **b181** `list_item` (Tooltip › Component level styling): template
- **b182** `list_item` (Tooltip › Component level styling): component
- **b183** `code` (Tooltip › Component level styling)

  ```
  <button type = "button" class = "btn btn-info"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus." >
  I have component level styling
  </button>
  ```

- **b184** `code` (Tooltip › Component level styling)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-styling-local' ,
  templateUrl : './styling-local.html' ,
  styles : [
  `
  : host . tooltip - inner {
  background - color : # 009688 ;
  color : # fff ;
  }
  : host . tooltip . top . tooltip - arrow : before ,
  : host . tooltip . top . tooltip - arrow {
  border - top - color : # 009688 ;
  }
  `
  ],
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipStylingLocalComponent {}
  ```

- **b185** `heading`: Custom class
- **b186** `paragraph` (Tooltip › Custom class): #
- **b187** `other` (Tooltip › Custom class): Demo with custom class
- **b188** `list_item` (Tooltip › Custom class): template
- **b189** `list_item` (Tooltip › Custom class): component
- **b190** `code` (Tooltip › Custom class)

  ```
  <button type = "button" class = "btn btn-primary"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus." containerClass = "customClass" >
  Demo with custom class
  </button>
  ```

- **b191** `code` (Tooltip › Custom class)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-class' ,
  templateUrl : './class.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipClassComponent {}
  ```

- **b192** `heading`: Tooltip with delay
- **b193** `paragraph` (Tooltip › Tooltip with delay): #
- **b194** `paragraph` (Tooltip › Tooltip with delay): Hold on cursor above button for 0,5 second or more to see delayed tooltip
- **b195** `other` (Tooltip › Tooltip with delay): Tooltip with 0.5sec delay
- **b196** `list_item` (Tooltip › Tooltip with delay): template
- **b197** `list_item` (Tooltip › Tooltip with delay): component
- **b198** `code` (Tooltip › Tooltip with delay)

  ```
  <button type = "button" class = "btn btn-primary"
  tooltip = "Vivamus sagittis lacus vel augue laoreet rutrum faucibus." [ delay ] = "500" >
  Tooltip with 0.5sec delay
  </button>
  ```

- **b199** `code` (Tooltip › Tooltip with delay)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-tooltip-delay' ,
  templateUrl : './delay.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoTooltipDelayComponent {}
  ```

- **b200** `heading`: Installation
- **b201** `code` (Tooltip › Installation)

  ```
  ng add ngx-bootstrap  --component tooltip
  ```

- **b202** `code` (Tooltip › Installation)

  ```
  ### Standalone component usage
  import { TooltipModule } from 'ngx-bootstrap/timepicker';
  
  @Component({
    standalone: true,
    imports: [TooltipModule,...]
  })
  export class AppComponent(){}
  
  ### Module usage
  import { TooltipModule } from 'ngx-bootstrap/tooltip';
  
  @NgModule({
    imports: [TooltipModule,...]
  })
  export class AppModule(){}
  ```

- **b203** `table_row` (Tooltip › Installation): Selector
- **b204** `heading`: TooltipConfig
- **b205** `paragraph` (Tooltip › Installation › TooltipConfig): Default values provider for tooltip
- **b206** `heading`: Properties
- **b207** `table_row` (Tooltip › Installation › Properties): adaptivePosition; Type: boolean Default value: true sets disable adaptive position
- **b208** `table_row` (Tooltip › Installation › Properties): container; Type: string | undefined a selector specifying the element the tooltip should be appended to.
- **b209** `table_row` (Tooltip › Installation › Properties): delay; Type: number Default value: 0 delay before showing the tooltip
- **b210** `table_row` (Tooltip › Installation › Properties): placement; Type: string Default value: top tooltip placement, supported positions: 'top', 'bottom', 'left', 'right'
- **b211** `table_row` (Tooltip › Installation › Properties): triggers; Type: string Default value: hover focus array of event names which triggers tooltip opening
- **b212** `heading`: Basic
- **b213** `card` (Tooltip › Installation › Basic): Simple demo
- **b214** `heading`: Placement
- **b215** `card` (Tooltip › Installation › Placement): Tooltip on top
- **b216** `card` (Tooltip › Installation › Placement): Tooltip on right
- **b217** `card` (Tooltip › Installation › Placement): Tooltip auto
- **b218** `card` (Tooltip › Installation › Placement): Tooltip on left
- **b219** `card` (Tooltip › Installation › Placement): Tooltip on bottom
- **b220** `heading`: Disable adaptive position
- **b221** `card` (Tooltip › Installation › Disable adaptive position): Tooltip on top
- **b222** `card` (Tooltip › Installation › Disable adaptive position): Tooltip on right
- **b223** `heading`: Dismiss on next click
- **b224** `card` (Tooltip › Installation › Dismiss on next click): Dismissible tooltip
- **b225** `heading`: Dynamic Content
- **b226** `card` (Tooltip › Installation › Dynamic Content): Simple binding
- **b227** `heading`: Custom content template
- **b228** `card` (Tooltip › Installation › Custom content template): TemplateRef binding
- **b229** `heading`: Dynamic Html
- **b230** `card` (Tooltip › Installation › Dynamic Html): Show me tooltip with html
- **b231** `heading`: Append to body
- **b232** `card` (Tooltip › Installation › Append to body): Default tooltip
- **b233** `card` (Tooltip › Installation › Append to body): Tooltip appended to body
- **b234** `heading`: Configuring defaults
- **b235** `card` (Tooltip › Installation › Configuring defaults): Preconfigured tooltip
- **b236** `heading`: Custom triggers
- **b237** `card` (Tooltip › Installation › Custom triggers): Desktop
- **b238** `card` (Tooltip › Installation › Custom triggers): Hover over me!
- **b239** `card` (Tooltip › Installation › Custom triggers): Mobile
- **b240** `card` (Tooltip › Installation › Custom triggers): Click on me!
- **b241** `heading`: Manual triggering
- **b242** `card` (Tooltip › Installation › Manual triggering): This text has attached tooltip
- **b243** `card` (Tooltip › Installation › Manual triggering): Show Hide Toggle
- **b244** `heading`: Component level styling
- **b245** `card` (Tooltip › Installation › Component level styling): I have component level styling
- **b246** `heading`: Custom class
- **b247** `card` (Tooltip › Installation › Custom class): Demo with custom class
- **b248** `heading`: Tooltip with delay
- **b249** `card` (Tooltip › Installation › Tooltip with delay): Tooltip with 0.5sec delay
- **b250** `other` (Tooltip › Installation › Tooltip with delay): components
- **b251** `list_item` (Tooltip › Installation › Tooltip with delay): TooltipDirective
- **b252** `list_item` (Tooltip › Installation › Tooltip with delay): TooltipConfig
